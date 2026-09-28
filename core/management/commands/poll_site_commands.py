from django.core.management.base import BaseCommand, CommandError

from core.site_federation_client import poll_once


class Command(BaseCommand):
    help = "轮询中心站点签名命令并回报执行结果"

    def handle(self, *args, **options):
        try:
            result = poll_once()
        except Exception as exc:
            raise CommandError("站点命令轮询失败") from exc
        self.stdout.write(f"执行 {result['executed']} 条，拒绝 {result['rejected']} 条")
