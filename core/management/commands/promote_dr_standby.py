from django.core.management.base import BaseCommand, CommandError

from core.disaster_recovery import is_read_only_node
from core.disaster_recovery_failover import poll_once


class Command(BaseCommand):
    help = "从 witness 获取新任期并在副本校验通过后提升只读热备"

    def handle(self, *args, **options):
        if not is_read_only_node():
            raise CommandError("当前节点不是只读热备")
        result = poll_once()
        if result.get("role") != "primary":
            raise CommandError(f"未接管：{result.get('error_code') or '其他节点仍持有租约或快照未通过校验'}")
        self.stdout.write(self.style.SUCCESS(f"节点已提升为主站，任期 {result['epoch']}"))
