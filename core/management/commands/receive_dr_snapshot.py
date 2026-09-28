from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.disaster_recovery_snapshot import SnapshotValidationError, install_snapshot


class Command(BaseCommand):
    help = "验证并安装一个手动提供的签名加密灾备快照到隔离副本目录"

    def add_arguments(self, parser):
        parser.add_argument("package", type=Path)

    def handle(self, *args, **options):
        package = options["package"].resolve()
        if not package.is_file():
            raise CommandError("快照文件不存在")
        try:
            active = install_snapshot(
                package,
                settings.TAVERN_DR_REPLICA_ROOT,
                expected_version=settings.TAVERN_APP_VERSION,
                expected_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
            )
        except SnapshotValidationError as exc:
            raise CommandError(f"快照验证或安装失败：{exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"快照已验证并安装到 {active}"))
