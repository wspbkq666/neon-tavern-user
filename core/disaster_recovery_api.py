import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .disaster_recovery import (
    WitnessProtocolError,
    WitnessUnavailable,
    get_local_lease,
    is_read_only_node,
    lease_write_allowed,
)
from .disaster_recovery_failover import FailoverError, _witness_client


@require_GET
def status(request):
    node_id = settings.TAVERN_NODE_ID
    lease = get_local_lease() if settings.TAVERN_DR_ENABLED else None
    now = datetime.now(timezone.utc)
    remaining = max(0, int(lease.expires_at.timestamp() - now.timestamp())) if lease else 0
    authority = {"status": "unavailable", "holder_id": "", "epoch": 0, "expires_at": None}
    authority_error = ""
    if not settings.TAVERN_DR_ENABLED:
        role = "standalone"
        error_code = ""
    else:
        try:
            witness_lease = _witness_client().status()
            if witness_lease and lease_write_allowed(
                witness_lease,
                witness_lease.holder_id,
                now,
                min_remaining_seconds=0,
            ):
                authority = {
                    "status": "confirmed",
                    "holder_id": witness_lease.holder_id,
                    "epoch": witness_lease.epoch,
                    "expires_at": int(witness_lease.expires_at.timestamp()),
                }
            else:
                authority_error = "lease_missing_or_expired"
        except (FailoverError, WitnessProtocolError, WitnessUnavailable, ValueError):
            authority_error = "witness_unavailable"

        local_is_authority = (
            lease is not None
            and authority["status"] == "confirmed"
            and lease_write_allowed(lease, node_id, now, min_remaining_seconds=0)
            and lease.holder_id == authority["holder_id"]
            and lease.epoch == authority["epoch"]
        )
        if is_read_only_node():
            role = "read_only"
            error_code = ""
        elif local_is_authority:
            role = "primary"
            error_code = ""
        elif authority["status"] != "confirmed":
            role = "unavailable"
            error_code = authority_error or "lease_missing_or_invalid"
        else:
            role = "read_only"
            error_code = "authority_mismatch"
    replication = {}
    try:
        value = json.loads(Path(settings.TAVERN_DR_STATUS_PATH).read_text(encoding="utf-8"))
        if isinstance(value, dict):
            replication = value
    except (OSError, ValueError, TypeError):
        pass
    last_successful = replication.get("last_successful_replication")
    snapshot_created_at = replication.get("snapshot_created_at")
    if replication.get("status") == "error":
        replica_status = "error"
        replication_error = replication.get("error_code", "replication_failed")
    elif last_successful:
        replica_status = "healthy"
        replication_error = ""
    elif settings.TAVERN_DR_ENABLED:
        replica_status = "stale"
        replication_error = "replication_not_yet_confirmed"
    else:
        replica_status = "not_configured"
        replication_error = ""
    if last_successful and not snapshot_created_at:
        replica_status = "stale"
        replication_error = "replication_status_invalid"
    lag_seconds = None
    if snapshot_created_at:
        try:
            lag_seconds = max(0, int((now - datetime.fromisoformat(snapshot_created_at)).total_seconds()))
            if lag_seconds > 3600 and replica_status == "healthy":
                replica_status = "stale"
                replication_error = "replication_lag_over_one_hour"
        except (TypeError, ValueError):
            last_successful = None
            replica_status = "stale"
            replication_error = "replication_status_invalid"
    response = JsonResponse({
        "node_id": node_id,
        "role": role,
        "epoch": lease.epoch if lease else 0,
        "lease_remaining_seconds": remaining,
        "authority": authority,
        "replica_status": replica_status,
        "last_successful_replication": last_successful,
        "replication_snapshot_at": snapshot_created_at,
        "replication_lag_seconds": lag_seconds,
        "replication_error_code": replication_error,
        "error_code": error_code,
    })
    origin = request.headers.get("Origin", "")
    if request.method == "GET" and origin in settings.TAVERN_DR_STATUS_ALLOWED_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"
    response["Cache-Control"] = "no-store"
    return response
