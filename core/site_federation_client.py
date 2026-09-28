import base64
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .admin_api import staff_error
from .auth_api import body_or_error
from .models import FederationIdentity, SiteCommandReceipt, SiteSettings
from .site_command_executor import apply_site_command
from .site_command_signing import SigningKeyUnavailable, load_private_key, verify_command
from .site_signing import canonical_message, validate_public_key


class FederationClientError(ValueError):
    pass


TARGET_PRIVATE_KEY_ENV_NAMES = {
    "main_154": "TAVERN_FEDERATION_PRIVATE_KEY_FILE",
    "main_123": "TAVERN_FEDERATION_MAIN_123_PRIVATE_KEY_FILE",
}
TARGET_CENTRAL_URLS = {
    "main_154": "https://154.222.26.47",
    "main_123": "https://123.56.125.209",
}
TARGET_PAIRING_CODE_FILE_ENV_NAMES = {
    "main_154": "TAVERN_FEDERATION_PAIRING_CODE_MAIN_154_FILE",
    "main_123": "TAVERN_FEDERATION_PAIRING_CODE_MAIN_123_FILE",
}


def active_federation_identities():
    return list(FederationIdentity.objects.filter(active=True).order_by("target_key"))


def dual_federation_registered():
    identities = {
        identity.target_key: identity
        for identity in FederationIdentity.objects.filter(active=True, target_key__in=TARGET_CENTRAL_URLS)
    }
    return all(
        key in identities and identities[key].central_url.rstrip("/") == central_url
        for key, central_url in TARGET_CENTRAL_URLS.items()
    )


def validate_target_central(identity):
    expected_url = TARGET_CENTRAL_URLS.get(identity.target_key)
    actual_url = validate_central_url(identity.central_url)
    if expected_url is None or actual_url != expected_url:
        raise FederationClientError("总站目标与固定主站地址不匹配")
    return actual_url


def _public_key(private):
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def validate_central_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise FederationClientError("中心地址无效")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise FederationClientError("中心地址必须为 HTTPS 站点根地址")
    try:
        port = parsed.port
    except ValueError as exc:
        raise FederationClientError("中心地址无效") from exc
    if parsed.hostname.casefold() in ("localhost", "127.0.0.1", "::1"):
        raise FederationClientError("中心地址必须为公网 HTTPS 地址")
    return value.rstrip("/")


def pair_with_central(central_url, pairing_code, site_name, *, target_key, declared_url=None, automatic=False, transport=None):
    central_url = validate_central_url(central_url)
    expected_url = TARGET_CENTRAL_URLS.get(target_key) if isinstance(target_key, str) else None
    if expected_url is None or central_url != expected_url:
        raise FederationClientError("总站目标与固定主站地址不匹配")
    if not isinstance(pairing_code, str) or not 20 <= len(pairing_code) <= 100:
        raise FederationClientError("配对码无效")
    private_key_env_name = TARGET_PRIVATE_KEY_ENV_NAMES.get(target_key) if isinstance(target_key, str) else None
    if private_key_env_name is None:
        raise FederationClientError("总站目标无效")
    existing = FederationIdentity.objects.filter(target_key=target_key).first()
    if existing:
        if existing.central_url == central_url and existing.active:
            return existing
        raise FederationClientError("该总站目标身份已停用或地址不匹配，需由总站管理员处理")
    private = load_private_key(private_key_env_name)
    market_site_id = SiteSettings.objects.get_or_create(pk=1)[0].market_site_id
    body = {
        "pairing_code": pairing_code, "name": site_name,
        "declared_url": declared_url or f"https://{site_name}/",
        "public_key": _public_key(private), "market_site_id": str(market_site_id),
    }
    endpoint = "enroll" if automatic else "register"
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
        response = client.post(f"{central_url}/api/federation/{endpoint}/", json=body)
    if response.status_code != 201:
        raise FederationClientError("中心站点拒绝配对")
    try:
        data = response.json()
        site_id = uuid.UUID(data["site_id"])
        public_key = validate_public_key(data["central_public_key"])
        if data["status"] != "active":
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise FederationClientError("中心配对响应无效") from exc
    with transaction.atomic():
        identity = FederationIdentity.objects.create(
            target_key=target_key, site_id=site_id, central_url=central_url,
            central_public_key=public_key, private_key_env_name=private_key_env_name,
        )
        from .chat_sync import seed_pending_chat_outbox
        from .user_directory_sync import seed_user_directory_snapshot

        seed_pending_chat_outbox(identity)
        seed_user_directory_snapshot(identity)
    return identity


def _read_pairing_code(target_key):
    env_name = TARGET_PAIRING_CODE_FILE_ENV_NAMES[target_key]
    credential_path = os.environ.get(env_name)
    if not credential_path:
        raise FederationClientError(f"缺少 {target_key} 一次性配对凭据")
    try:
        code = Path(credential_path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise FederationClientError(f"无法读取 {target_key} 一次性配对凭据") from exc
    if not 20 <= len(code) <= 100:
        raise FederationClientError(f"{target_key} 一次性配对凭据无效")
    return code


def enroll_with_centrals(site_name, declared_url):
    declared_url = validate_central_url(declared_url)
    identities = {}
    for target_key, central_url in TARGET_CENTRAL_URLS.items():
        existing = FederationIdentity.objects.filter(target_key=target_key).first()
        if existing:
            if existing.active and existing.central_url.rstrip("/") == central_url:
                identities[target_key] = existing
                continue
            raise FederationClientError(f"{target_key} 已有停用或不匹配的配对身份")
        identity = pair_with_central(
            central_url,
            _read_pairing_code(target_key),
            site_name,
            target_key=target_key,
            declared_url=declared_url,
            automatic=True,
        )
        identities[target_key] = identity
    if not dual_federation_registered():
        raise FederationClientError("双主站登记未完成")
    return identities


@require_POST
def pair_site(request):
    error = staff_error(request)
    if error:
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if set(data) not in ({"central_url", "pairing_code"}, {"central_url", "pairing_code", "target_key"}):
        return JsonResponse({"error": "请填写总站地址、一次性配对码和可选的总站目标"}, status=400)
    try:
        identity = pair_with_central(
            data["central_url"], data["pairing_code"], request.get_host(),
            target_key=data.get("target_key", "main_154"),
        )
    except (FederationClientError, SigningKeyUnavailable) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except httpx.HTTPError:
        return JsonResponse({"error": "中心站点暂时不可连接"}, status=502)
    return JsonResponse({"target_key": identity.target_key, "site_id": str(identity.site_id), "status": "active"}, status=201)


@require_GET
def federation_status(request):
    error = staff_error(request)
    if error:
        return error
    from .models import ChatSyncOutbox, UserDirectoryOutbox

    identities = {identity.target_key: identity for identity in FederationIdentity.objects.all()}
    targets = []
    for target_key, central_url in TARGET_CENTRAL_URLS.items():
        identity = identities.get(target_key)
        chat_count = ChatSyncOutbox.objects.filter(site_id=identity.site_id).count() if identity else 0
        user_count = UserDirectoryOutbox.objects.filter(site_id=identity.site_id).count() if identity else 0
        chat_error = ChatSyncOutbox.objects.filter(
            site_id=identity.site_id,
        ).exclude(last_error="").order_by("-id").values_list("last_error", flat=True).first() if identity else ""
        user_error = UserDirectoryOutbox.objects.filter(
            site_id=identity.site_id,
        ).exclude(last_error="").order_by("-id").values_list("last_error", flat=True).first() if identity else ""
        error_code = chat_error or user_error or (identity.last_error_code if identity else "")
        if error_code not in {"network_error", "connection_failed", "credentials_unavailable", "sync_rejected"}:
            error_code = "sync_failed" if error_code else ""
        targets.append({
            "target_key": target_key,
            "paired": identity is not None,
            "active": bool(identity and identity.active and identity.central_url.rstrip("/") == central_url),
            "last_success_at": identity.last_success_at.isoformat() if identity and identity.last_success_at else None,
            "pending_chat": chat_count,
            "pending_users": user_count,
            "last_error_code": error_code,
        })
    return JsonResponse({"targets": targets})


def _signed_headers(identity, private, method, path, body):
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    message = canonical_message(str(identity.site_id), method, path, timestamp, nonce, body)
    signature = base64.urlsafe_b64encode(private.sign(message)).decode("ascii").rstrip("=")
    return {
        "X-Federation-Site": str(identity.site_id), "X-Federation-Time": timestamp,
        "X-Federation-Nonce": nonce, "X-Federation-Signature": signature,
    }


def poll_once(*, transport=None):
    identities = {
        identity.target_key: identity
        for identity in FederationIdentity.objects.filter(active=True, target_key__in=TARGET_CENTRAL_URLS)
    }
    if not identities:
        return {"executed": 0, "rejected": 0}
    result = {"executed": 0, "rejected": 0}
    failures = []
    path = "/api/federation/commands/"
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
        for target_key in TARGET_CENTRAL_URLS:
            identity = identities.get(target_key)
            if identity is None:
                continue
            try:
                central_url = validate_target_central(identity)
                private = load_private_key(identity.private_key_env_name)
                response = client.get(central_url + path, headers=_signed_headers(identity, private, "GET", path, b""))
                response.raise_for_status()
                commands = response.json().get("commands")
                if not isinstance(commands, list) or len(commands) > 1000:
                    raise FederationClientError("中心命令响应无效")
                for envelope in commands:
                    if not verify_command(envelope, identity.central_public_key):
                        result["rejected"] += 1
                        continue
                    applied = apply_site_command(envelope, identity=identity)
                    try:
                        command_id = uuid.UUID(envelope["payload"]["command_id"])
                    except (ValueError, TypeError, KeyError):
                        result["rejected"] += 1
                        continue
                    if not SiteCommandReceipt.objects.filter(command_id=command_id).exists():
                        result["rejected"] += 1
                        continue
                    if applied["status"] == "expired":
                        result["rejected"] += 1
                        continue
                    if applied["status"] == "executed":
                        result["executed"] += 1
                    report_path = f"/api/federation/commands/{command_id}/result/"
                    report_body = json.dumps({
                        "status": "succeeded" if applied["status"] == "executed" else "failed",
                        "result": {"ok": applied["status"] == "executed"},
                    }, separators=(",", ":")).encode("utf-8")
                    reported = client.post(
                        central_url + report_path, content=report_body,
                        headers={**_signed_headers(identity, private, "POST", report_path, report_body), "Content-Type": "application/json"},
                    )
                    reported.raise_for_status()
                    if envelope["payload"]["action"] == "revoke_site" and applied["status"] == "executed":
                        FederationIdentity.objects.filter(pk=identity.pk, active=True).update(active=False)
            except (httpx.HTTPError, FederationClientError, SigningKeyUnavailable, ValueError) as exc:
                failures.append((target_key, exc))
    if failures:
        if len(failures) == 1:
            raise failures[0][1]
        failed_targets = "、".join(target for target, _ in failures)
        raise FederationClientError(f"以下总站的命令轮询失败：{failed_targets}") from failures[0][1]
    return result
