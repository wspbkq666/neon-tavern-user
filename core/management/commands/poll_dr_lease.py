from django.core.management.base import BaseCommand, CommandError

from core.disaster_recovery_failover import poll_once


class Command(BaseCommand):
    help = "核对 witness 主节点并续租；租约或仲裁不可确认时切为只读"

    def handle(self, *args, **options):
        try:
            result = poll_once()
        except Exception as exc:
            raise CommandError("仲裁轮询失败，节点保持只读") from exc
        role = result["role"]
        if role == "primary":
            self.stdout.write(self.style.SUCCESS(f"主节点可写，任期 {result['epoch']}"))
        elif role == "read_only":
            self.stdout.write(self.style.WARNING(f"只读备用节点，当前任期 {result['epoch']}"))
        else:
            self.stdout.write(self.style.ERROR("仲裁不可用，节点已切为只读"))
