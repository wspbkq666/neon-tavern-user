from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.disaster_recovery import get_local_lease, is_read_only_node
from core.disaster_recovery_snapshot import SnapshotValidationError, create_snapshot, validate_snapshot
from core.disaster_recovery_transfer import ReplicationTransferError, send_snapshot, write_replication_status


class Command(BaseCommand):
    help = "生成、发送并保留本站的签名加密灾备快照"

    def handle(self, *args, **options):
        if not settings.TAVERN_DR_ENABLED or is_read_only_node():
            raise CommandError("当前节点未启用灾备写入角色，不能创建快照")
        lease = get_local_lease()
        if lease is None:
            raise CommandError("当前节点没有有效写租约，不能创建快照")
        directory = Path(settings.TAVERN_DR_SNAPSHOT_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        package = directory / f"{stamp}-{uuid4().hex}.drsnap"
        try:
            create_snapshot(package, lease)
            if settings.TAVERN_DR_PEER_URL:
                manifest = validate_snapshot(
                    package,
                    expected_version=settings.TAVERN_APP_VERSION,
                    expected_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
                )
                result = send_snapshot(package, settings.TAVERN_DR_PEER_URL)
                if (result.get("status"), result.get("snapshot_id"), result.get("epoch")) != (
                    "replicated", manifest.snapshot_id, manifest.epoch
                ):
                    raise ReplicationTransferError("热备未确认快照接收")
                self.stdout.write(f"快照已复制至热备：任期 {result['epoch']}，快照 {result['snapshot_id']}")
            else:
                self.stdout.write(self.style.WARNING("本地快照已生成，但未配置热备 HTTPS 地址，尚未完成异地复制"))
            self._prune(directory, keep=24)
        except (SnapshotValidationError, ReplicationTransferError, OSError) as exc:
            if settings.TAVERN_DR_PEER_URL:
                write_replication_status(status="error", error_code="replication_failed")
            raise CommandError(f"灾备快照未完成：{exc}") from exc
        self.stdout.write(f"本地加密快照：{package.name}")

    @staticmethod
    def _prune(directory: Path, *, keep: int):
        packages = sorted(directory.glob("*.drsnap"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in packages[keep:]:
            stale.unlink()
