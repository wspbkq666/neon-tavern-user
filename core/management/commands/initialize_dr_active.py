import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "在维护窗口内，把当前数据库和媒体安全复制到容灾活动目录"

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-maintenance",
            action="store_true",
            help="确认网站写入服务已停止，避免复制期间产生遗漏",
        )

    def handle(self, *args, **options):
        if not options["confirm_maintenance"]:
            raise CommandError("请先停止本站写入服务，再加 --confirm-maintenance 确认维护窗口")
        if settings.TAVERN_DR_ENABLED or settings.TAVERN_DR_USE_REPLICA_ACTIVE:
            raise CommandError("仅能在容灾关闭且尚未使用活动副本时初始化")

        root = Path(settings.TAVERN_DR_REPLICA_ROOT)
        if root.is_symlink():
            raise CommandError("活动副本根目录不能是符号链接")
        active = root / "active"
        if active.exists() or active.is_symlink():
            raise CommandError("活动副本已存在；拒绝覆盖")

        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".initialize-", dir=root))
        try:
            self._backup_database(staging / "database.sqlite3")
            self._copy_media(staging / "media")
            database_file = (staging / "database.sqlite3").open("rb+")
            try:
                os.fsync(database_file.fileno())
            finally:
                database_file.close()
            staging.replace(active)
        except (OSError, sqlite3.DatabaseError, RuntimeError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise CommandError(f"活动副本初始化失败，原数据库和媒体未修改：{exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"初始副本已安装：{active}"))

    @staticmethod
    def _backup_database(destination):
        connection = connections["default"]
        connection.ensure_connection()
        target = sqlite3.connect(destination)
        try:
            connection.connection.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("在线备份 SQLite 完整性检查未通过")
            target.commit()
        finally:
            target.close()

    @staticmethod
    def _copy_media(destination):
        source = Path(settings.MEDIA_ROOT)
        if not source.exists():
            destination.mkdir(parents=True)
            return
        if source.is_symlink():
            raise RuntimeError("媒体目录不能是符号链接")
        for path in source.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("媒体目录包含符号链接，已停止初始化")
        shutil.copytree(source, destination, symlinks=True)
