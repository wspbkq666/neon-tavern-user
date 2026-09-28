import base64
import binascii
import hashlib
import re
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .disaster_recovery import canonical_request_message
from .disaster_recovery_snapshot import (
    SnapshotValidationError,
    _sha256,
    _trusted_public_key,
    install_snapshot,
    validate_snapshot,
)
from .disaster_recovery import is_read_only_node


_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")


class ReplicationTransferError(RuntimeError):
    pass


def write_replication_status(*, status, snapshot_id="", epoch=0, snapshot_created_at="", error_code=""):
    path = Path(settings.TAVERN_DR_STATUS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    previous = {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    payload = {
        "status": status,
        "snapshot_id": snapshot_id or previous.get("snapshot_id", ""),
        "epoch": int(epoch or previous.get("epoch", 0)),
        "last_successful_replication": (
            datetime.now(timezone.utc).isoformat() if status == "healthy"
            else previous.get("last_successful_replication")
        ),
        "snapshot_created_at": snapshot_created_at or previous.get("snapshot_created_at"),
        "error_code": error_code,
    }
    try:
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=True, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _signature_bytes(value):
    if not isinstance(value, str) or not _SIGNATURE.fullmatch(value):
        raise ValueError("签名格式无效")
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise ValueError("签名格式无效")
    return raw


def _canonical_digest_message(node_id, method, path, timestamp, request_id, body_digest):
    return f"{node_id}\n{method.upper()}\n{path}\n{timestamp}\n{request_id}\n{body_digest}".encode("ascii")


def sign_replication_request(private_key, node_id, method, path, timestamp, request_id, body):
    if not isinstance(private_key, Ed25519PrivateKey):
        private_key = Ed25519PrivateKey.from_private_bytes(private_key)
    message = canonical_request_message(node_id, method, path, timestamp, request_id, body)
    return {
        "X-DR-Node": node_id,
        "X-DR-Time": timestamp,
        "X-DR-Request-Id": request_id,
        "X-DR-SHA256": hashlib.sha256(body).hexdigest(),
        "X-DR-Signature": base64.urlsafe_b64encode(private_key.sign(message)).decode("ascii").rstrip("="),
    }


def _signed_headers_for_file(private_key, node_id, method, path, file_path):
    timestamp = str(int(time.time()))
    request_id = uuid.uuid4().hex
    digest = _sha256(file_path)
    signature = private_key.sign(
        _canonical_digest_message(node_id, method, path, timestamp, request_id, digest)
    )
    return {
        "X-DR-Node": node_id,
        "X-DR-Time": timestamp,
        "X-DR-Request-Id": request_id,
        "X-DR-SHA256": digest,
        "X-DR-Signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
    }


def send_snapshot(package: Path, destination_url: str, *, timeout=120, transport=None):
    parsed = urlsplit(destination_url)
    allow_personal_http = (
        parsed.scheme == "http"
        and settings.TAVERN_DR_ALLOW_HTTP_PEER
        and parsed.hostname
        and parsed.hostname.lower() in settings.TAVERN_DR_HTTP_PEER_HOSTS
    )
    if (
        (parsed.scheme != "https" and not allow_personal_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ReplicationTransferError("复制目标必须为 HTTPS；个人 HTTP 模式仅允许显式列出的目标 IP")
    path = "/api/disaster-recovery/replica/"
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    encoded = settings.TAVERN_DR_NODE_PRIVATE_KEY
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ReplicationTransferError("本节点签名密钥配置无效") from exc
    headers = _signed_headers_for_file(private_key, settings.TAVERN_NODE_ID, "POST", path, package)
    try:
        with Path(package).open("rb") as snapshot, httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
        ) as client:
            response = client.post(
                destination_url.rstrip("/") + path,
                files={"snapshot": (Path(package).name, snapshot, "application/octet-stream")},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise ReplicationTransferError("备用节点快照传输失败") from exc
    if response.status_code != 200:
        raise ReplicationTransferError(f"备用节点拒绝快照（HTTP {response.status_code}）")
    try:
        result = response.json()
    except ValueError as exc:
        raise ReplicationTransferError("备用节点返回了无效确认") from exc
    if result.get("status") != "replicated" or not result.get("snapshot_id") or not result.get("created_at"):
        raise ReplicationTransferError("备用节点返回的快照确认字段无效")
    write_replication_status(
        status="healthy", snapshot_id=result["snapshot_id"], epoch=result.get("epoch", 0),
        snapshot_created_at=result["created_at"],
    )
    return result


def _authenticate_request(request, snapshot_file):
    node_id = request.headers.get("X-DR-Node", "")
    timestamp = request.headers.get("X-DR-Time", "")
    request_id = request.headers.get("X-DR-Request-Id", "")
    digest = request.headers.get("X-DR-SHA256", "")
    signature = request.headers.get("X-DR-Signature", "")
    if (
        not node_id or node_id == settings.TAVERN_NODE_ID
        or not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 11
        or not _REQUEST_ID.fullmatch(request_id)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
        or abs(int(time.time()) - int(timestamp)) > 60
    ):
        return False
    if _sha256(snapshot_file) != digest:
        return False
    try:
        public_key = _trusted_public_key(node_id)
        public_key.verify(
            _signature_bytes(signature),
            _canonical_digest_message(node_id, request.method, request.path, timestamp, request_id, digest),
        )
    except (InvalidSignature, SnapshotValidationError, ValueError, binascii.Error):
        return False
    return True


@csrf_exempt
@require_POST
def receive_snapshot(request):
    if not settings.TAVERN_DR_ENABLED or not is_read_only_node():
        return JsonResponse({"error_code": "replica_not_read_only"}, status=409)
    snapshot = request.FILES.get("snapshot")
    if snapshot is None:
        return JsonResponse({"error_code": "snapshot_missing"}, status=400)
    max_bytes = int(getattr(settings, "TAVERN_DR_MAX_SNAPSHOT_BYTES", 2 * 1024 * 1024 * 1024))
    if snapshot.size <= 0 or snapshot.size > max_bytes:
        return JsonResponse({"error_code": "snapshot_size_invalid"}, status=413)
    replica_root = Path(settings.TAVERN_DR_REPLICA_ROOT)
    replica_root.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".incoming-", suffix=".drsnap", dir=replica_root, delete=False) as target:
            temporary_path = Path(target.name)
            for chunk in snapshot.chunks():
                target.write(chunk)
        if not _authenticate_request(request, temporary_path):
            return JsonResponse({"error_code": "replica_signature_invalid"}, status=401)
        manifest = validate_snapshot(
            temporary_path,
            expected_version=settings.TAVERN_APP_VERSION,
            expected_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
        )
        if manifest.source_node_id != request.headers.get("X-DR-Node"):
            return JsonResponse({"error_code": "snapshot_source_mismatch"}, status=401)
        install_snapshot(
            temporary_path,
            replica_root,
            expected_version=settings.TAVERN_APP_VERSION,
            expected_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
        )
        if is_read_only_node():
            from .disaster_recovery import set_local_role

            set_local_role("read_only")
        write_replication_status(
            status="healthy", snapshot_id=manifest.snapshot_id, epoch=manifest.epoch,
            snapshot_created_at=manifest.created_at,
        )
        return JsonResponse({
            "status": "replicated",
            "snapshot_id": manifest.snapshot_id,
            "epoch": manifest.epoch,
            "created_at": manifest.created_at,
        })
    except SnapshotValidationError as exc:
        write_replication_status(status="error", error_code="snapshot_rejected")
        return JsonResponse({"error_code": "snapshot_rejected", "error": str(exc)}, status=422)
    except OSError:
        write_replication_status(status="error", error_code="replica_storage_error")
        return JsonResponse({"error_code": "replica_storage_error"}, status=507)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
