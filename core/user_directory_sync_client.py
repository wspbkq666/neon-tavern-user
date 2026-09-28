import json
from datetime import timedelta

import httpx
from django.db import transaction
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import FederationIdentity, UserDirectoryOutbox, UserProfile
from .site_command_signing import SigningKeyUnavailable, load_private_key
from .site_federation_client import _signed_headers, active_federation_identities, validate_target_central


MAX_USERS = 1000
MAX_BYTES = 1_048_576
RETRY_LIMIT_SECONDS = 300


def _batch(identity, now):
    queued_user_ids = UserDirectoryOutbox.objects.filter(
        site_id=identity.site_id,
    ).values_list("source_user_id", flat=True).distinct()
    consented_user_ids = {
        str(user_id) for user_id in UserProfile.objects.filter(
            user_id__in=queued_user_ids,
            policy_consent_at__isnull=False,
            privacy_policy_version=PRIVACY_POLICY_VERSION,
            usage_rules_version=USAGE_RULES_VERSION,
        ).values_list("user_id", flat=True)
    }
    unconsented_user_ids = set(queued_user_ids) - consented_user_ids
    if unconsented_user_ids:
        UserDirectoryOutbox.objects.filter(
            site_id=identity.site_id,
            source_user_id__in=unconsented_user_ids,
        ).delete()
    first = UserDirectoryOutbox.objects.filter(site_id=identity.site_id).order_by("id").first()
    if first is None:
        return None
    if first.next_attempt_at > now:
        return None
    latest = {}
    cursor = None
    count = 0
    rows = UserDirectoryOutbox.objects.filter(site_id=identity.site_id, id__gte=first.id).order_by("id")
    for row in rows.iterator(chunk_size=MAX_USERS):
        if row.next_attempt_at > now or count >= MAX_USERS:
            break
        candidate = dict(latest)
        candidate[row.source_user_id] = row
        users = [entry.payload for entry in sorted(candidate.values(), key=lambda entry: entry.id)]
        encoded = json.dumps(
            {"cursor": row.id, "users": users},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            break
        latest = candidate
        cursor = row.id
        count += 1
    if cursor is None:
        return None
    return {"cursor": cursor, "users": [entry.payload for entry in sorted(latest.values(), key=lambda entry: entry.id)]}


def _schedule_retry(site_id, cursor, error_code):
    now = timezone.now()
    with transaction.atomic():
        rows = list(UserDirectoryOutbox.objects.select_for_update().filter(site_id=site_id, id__lte=cursor))
        for row in rows:
            delay = min(5 * (2 ** min(row.attempt_count, 6)), RETRY_LIMIT_SECONDS)
            row.attempt_count += 1
            row.next_attempt_at = now + timedelta(seconds=delay)
            row.last_error = error_code[:120]
            row.save(update_fields=["attempt_count", "next_attempt_at", "last_error"])


def _sync_target(identity, *, transport=None):
    result = {
        "target_key": identity.target_key,
        "sent": 0,
        "acknowledged": 0,
        "pending": UserDirectoryOutbox.objects.filter(site_id=identity.site_id).count(),
        "error": "",
    }
    batch = _batch(identity, timezone.now())
    if batch is None:
        return result
    body = json.dumps(batch, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        central_url = validate_target_central(identity)
        private = load_private_key(identity.private_key_env_name)
        path = "/api/federation/users/sync/"
        headers = _signed_headers(identity, private, "POST", path, body)
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False, transport=transport) as client:
            response = client.post(
                central_url + path,
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
            response.raise_for_status()
        acknowledgement = response.json()
        if acknowledgement != {"cursor": batch["cursor"]}:
            raise ValueError("中心确认游标不匹配")
    except (httpx.HTTPError, ValueError, SigningKeyUnavailable) as exc:
        error = (
            "network_error" if isinstance(exc, httpx.HTTPError)
            else "credentials_unavailable" if isinstance(exc, SigningKeyUnavailable)
            else "sync_rejected"
        )
        _schedule_retry(identity.site_id, batch["cursor"], error)
        FederationIdentity.objects.filter(pk=identity.pk).update(last_error_code=error)
        result.update(sent=len(batch["users"]), error=error)
        result["pending"] = UserDirectoryOutbox.objects.filter(site_id=identity.site_id).count()
        return result

    with transaction.atomic():
        deleted, _ = UserDirectoryOutbox.objects.filter(
            site_id=identity.site_id, id__lte=batch["cursor"],
        ).delete()
        FederationIdentity.objects.filter(pk=identity.pk).update(
            last_success_at=timezone.now(), last_error_code="",
        )
    result.update(sent=len(batch["users"]), acknowledged=deleted)
    result["pending"] = UserDirectoryOutbox.objects.filter(site_id=identity.site_id).count()
    return result


def sync_user_directory_once(*, transport=None):
    targets = [_sync_target(identity, transport=transport) for identity in active_federation_identities()]
    result = {
        "targets": targets,
        "sent": sum(target["sent"] for target in targets),
        "acknowledged": sum(target["acknowledged"] for target in targets),
        "pending": UserDirectoryOutbox.objects.count(),
    }
    errors = [target["error"] for target in targets if target["error"]]
    if errors:
        result["error"] = errors[0]
    return result
