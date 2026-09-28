import json
from datetime import timedelta

import httpx
from django.db import transaction
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import ChatSyncOutbox, FederationIdentity, UserProfile
from .site_command_signing import SigningKeyUnavailable, load_private_key
from .site_federation_client import (
    _signed_headers,
    active_federation_identities,
    validate_target_central,
)


MAX_EVENTS = 100
MAX_BYTES = 1_048_576
RETRY_LIMIT_SECONDS = 300


def _batch(site_id, now):
    while True:
        rows = list(ChatSyncOutbox.objects.filter(site_id=site_id).order_by("id")[:MAX_EVENTS])
        if not rows:
            return []
        source_user_ids = {row.payload.get("source_user_id") for row in rows if row.payload.get("source_user_id")}
        consented_user_ids = {
            str(user_id) for user_id in UserProfile.objects.filter(
                user_id__in=source_user_ids,
                policy_consent_at__isnull=False,
                privacy_policy_version=PRIVACY_POLICY_VERSION,
                usage_rules_version=USAGE_RULES_VERSION,
            ).values_list("user_id", flat=True)
        }
        unconsented = source_user_ids - consented_user_ids
        if unconsented:
            ChatSyncOutbox.objects.filter(site_id=site_id, payload__source_user_id__in=unconsented).delete()
            continue
        if rows[0].next_attempt_at > now:
            return []
        latest_by_message = {}
        for row in rows:
            if row.next_attempt_at > now:
                break
            latest_by_message[row.source_message_id] = row
        ordered = sorted(latest_by_message.values(), key=lambda row: row.pk)
        result = []
        for row in ordered:
            candidate = {
                "cursor": row.pk,
                "event_type": row.event_type,
                "payload": row.payload,
            }
            encoded = json.dumps(
                {"events": result + [candidate], "cursor": row.pk},
                ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_BYTES:
                break
            result.append(candidate)
        return result


def _schedule_retry(site_id, cursor, error_code):
    now = timezone.now()
    with transaction.atomic():
        rows = list(ChatSyncOutbox.objects.select_for_update().filter(site_id=site_id, pk__lte=cursor))
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
        "pending": ChatSyncOutbox.objects.filter(site_id=identity.site_id).count(),
        "error": "",
    }
    events = _batch(identity.site_id, timezone.now())
    if not events:
        return result
    body = json.dumps(
        {"events": events, "cursor": events[-1]["cursor"]},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    try:
        central_url = validate_target_central(identity)
        private = load_private_key(identity.private_key_env_name)
        path = "/api/federation/chat-events/"
        headers = _signed_headers(identity, private, "POST", path, body)
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False, transport=transport) as client:
            response = client.post(
                central_url + path,
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
            response.raise_for_status()
        acknowledgement = response.json()
        expected_ids = [event["payload"]["source_message_id"] for event in events]
        if acknowledgement != {"cursor": events[-1]["cursor"], "accepted": expected_ids}:
            raise ValueError("中心确认游标或消息集合不匹配")
    except (httpx.HTTPError, ValueError, SigningKeyUnavailable) as exc:
        error = (
            "network_error" if isinstance(exc, httpx.HTTPError)
            else "credentials_unavailable" if isinstance(exc, SigningKeyUnavailable)
            else "sync_rejected"
        )
        _schedule_retry(identity.site_id, events[-1]["cursor"], error)
        FederationIdentity.objects.filter(pk=identity.pk).update(last_error_code=error)
        result.update(sent=len(events), error=error)
        result["pending"] = ChatSyncOutbox.objects.filter(site_id=identity.site_id).count()
        return result

    with transaction.atomic():
        deleted, _ = ChatSyncOutbox.objects.filter(
            site_id=identity.site_id, pk__lte=events[-1]["cursor"],
        ).delete()
        FederationIdentity.objects.filter(pk=identity.pk).update(
            last_success_at=timezone.now(), last_error_code="",
        )
    result.update(sent=len(events), acknowledged=deleted)
    result["pending"] = ChatSyncOutbox.objects.filter(site_id=identity.site_id).count()
    return result


def sync_once(*, transport=None):
    targets = [_sync_target(identity, transport=transport) for identity in active_federation_identities()]
    result = {
        "targets": targets,
        "sent": sum(target["sent"] for target in targets),
        "acknowledged": sum(target["acknowledged"] for target in targets),
        "pending": ChatSyncOutbox.objects.count(),
    }
    errors = [target["error"] for target in targets if target["error"]]
    if errors:
        result["error"] = errors[0]
    return result
