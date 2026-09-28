import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.db.migrations.loader import MigrationLoader

from .disaster_recovery import (
    Lease,
    LeaseHeldByAnotherNode,
    WitnessClient,
    WitnessUnavailable,
    WitnessProtocolError,
    get_local_lease,
    is_read_only_node,
    lease_to_dict,
    lease_write_allowed,
    save_local_lease,
    set_local_role,
)
from .disaster_recovery_snapshot import (
    SnapshotManifest,
    SnapshotValidationError,
    create_snapshot,
    validate_installed_replica,
)
from .disaster_recovery_transfer import ReplicationTransferError, send_snapshot


class FailoverError(RuntimeError):
    pass


def _expected_migrations():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return tuple(sorted(f"{app}:{name}" for app, name in loader.disk_migrations))


def _witness_client():
    encoded_private = settings.TAVERN_DR_NODE_PRIVATE_KEY
    encoded_public = settings.TAVERN_WITNESS_PUBLIC_KEY
    try:
        private = base64.b64decode(encoded_private + "=" * (-len(encoded_private) % 4), altchars=b"-_", validate=True)
        public = base64.b64decode(encoded_public + "=" * (-len(encoded_public) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise FailoverError("节点或仲裁器签名密钥配置无效") from exc
    if len(private) != 32 or len(public) != 32:
        raise FailoverError("节点或仲裁器签名密钥配置无效")
    return WitnessClient(
        settings.TAVERN_WITNESS_URL,
        settings.TAVERN_NODE_ID,
        Ed25519PrivateKey.from_private_bytes(private),
        Ed25519PublicKey.from_public_bytes(public),
        ca_bundle=settings.TAVERN_WITNESS_CA_BUNDLE or None,
    )


def _validate_replica_snapshot():
    manifest = validate_installed_replica(
        settings.TAVERN_DR_REPLICA_ROOT,
        expected_version=settings.TAVERN_APP_VERSION,
        expected_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
        expected_migrations=_expected_migrations(),
    )
    created_at = datetime.fromisoformat(manifest.created_at)
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None or created_at > now + timedelta(seconds=2):
        raise FailoverError("热备快照时间无效")
    if now - created_at > timedelta(seconds=3600):
        raise FailoverError("热备快照已超过一小时，自动接管已停止以避免使用过旧数据")
    return manifest


def promote_node(node_id: str, lease: Lease, snapshot_manifest: SnapshotManifest | None = None):
    if node_id != settings.TAVERN_NODE_ID or lease.holder_id != node_id:
        raise FailoverError("新租约持有者与本节点不一致")
    if not is_read_only_node():
        raise FailoverError("只有只读热备可以执行接管")
    if not lease_write_allowed(lease, node_id, datetime.now(timezone.utc), min_remaining_seconds=10):
        raise FailoverError("新写租约无效或剩余时间不足")
    manifest = _validate_replica_snapshot()
    if snapshot_manifest is not None and snapshot_manifest.snapshot_id != manifest.snapshot_id:
        raise FailoverError("待接管快照与已验证副本不一致")
    if lease.epoch <= manifest.epoch:
        raise FailoverError("新租约任期没有高于快照任期")
    save_local_lease(lease)
    set_local_role("primary")
    return {"node_id": node_id, "epoch": lease.epoch, "snapshot_id": manifest.snapshot_id, "write_enabled": True}


def poll_once(client=None):
    node_id = settings.TAVERN_NODE_ID
    try:
        client = client or _witness_client()
        current = client.status()
        now = datetime.now(timezone.utc)
        if current and current.holder_id != node_id and lease_write_allowed(current, current.holder_id, now, min_remaining_seconds=0):
            set_local_role("read_only")
            return {"role": "read_only", "holder_id": current.holder_id, "epoch": current.epoch}
        if current and current.holder_id == node_id and lease_write_allowed(current, node_id, now, min_remaining_seconds=10):
            lease = client.renew(current)
        else:
            lease = client.acquire()
        save_local_lease(lease)
        if is_read_only_node():
            result = promote_node(node_id, lease)
            return {"role": "primary", **result}
        return {"role": "primary", "node_id": node_id, "epoch": lease.epoch, "write_enabled": True}
    except LeaseHeldByAnotherNode:
        set_local_role("read_only")
        try:
            latest = client.status()
        except (WitnessUnavailable, WitnessProtocolError):
            return {"role": "unavailable", "error_code": "witness_unavailable"}
        return {"role": "read_only", "holder_id": latest.holder_id if latest else "", "epoch": latest.epoch if latest else 0}
    except (WitnessUnavailable, WitnessProtocolError, ValueError):
        set_local_role("read_only")
        return {"role": "unavailable", "error_code": "witness_unavailable"}
    except (SnapshotValidationError, FailoverError) as exc:
        set_local_role("read_only")
        return {"role": "read_only", "error_code": "snapshot_invalid", "detail": str(exc)}


def prepare_failback(current_primary: str, recovered_node: str, *, destination_url=None, client=None):
    if current_primary != settings.TAVERN_NODE_ID or recovered_node == current_primary:
        raise FailoverError("回切节点参数无效")
    if is_read_only_node():
        raise FailoverError("当前节点不是可写主站")
    lease = get_local_lease()
    if lease is None or lease.holder_id != current_primary:
        raise FailoverError("当前主站没有有效租约，不能发起回切")
    peer_url = destination_url or settings.TAVERN_DR_PEER_URL
    if not peer_url:
        raise FailoverError("未配置恢复节点 HTTPS 地址")
    client = client or _witness_client()

    set_local_role("read_only")
    snapshot_dir = Path(settings.TAVERN_DR_SNAPSHOT_DIR)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    package = snapshot_dir / f"failback-{uuid4().hex}.drsnap"
    try:
        create_snapshot(package, lease)
        from .disaster_recovery_snapshot import validate_snapshot

        package_manifest = validate_snapshot(
            package,
            expected_version=settings.TAVERN_APP_VERSION,
            expected_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
            expected_migrations=_expected_migrations(),
        )
        result = send_snapshot(package, peer_url)
        if (result.get("status"), result.get("snapshot_id"), result.get("epoch")) != (
            "replicated", package_manifest.snapshot_id, package_manifest.epoch
        ):
            raise ReplicationTransferError("恢复节点没有确认最新快照")
        new_lease = client.handoff(recovered_node)
        save_local_lease(new_lease)
        return {
            "status": "handed_off",
            "current_primary": current_primary,
            "new_primary": recovered_node,
            "epoch": new_lease.epoch,
            "snapshot_id": package_manifest.snapshot_id,
        }
    except Exception:
        try:
            latest = client.status()
        except (WitnessUnavailable, WitnessProtocolError):
            latest = None
        if (
            latest
            and latest.holder_id == current_primary
            and latest.epoch == lease.epoch
            and lease_write_allowed(latest, current_primary, datetime.now(timezone.utc), min_remaining_seconds=10)
        ):
            save_local_lease(latest)
            set_local_role("primary")
        raise
