from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.disaster_recovery_failover import FailoverError, prepare_failback


class Command(BaseCommand):
    help = "冻结当前主站写入、同步恢复节点并向 witness 请求受控回切"

    def add_arguments(self, parser):
        parser.add_argument("recovered_node")
        parser.add_argument("--destination-url", default=None)

    def handle(self, *args, **options):
        try:
            result = prepare_failback(
                settings.TAVERN_NODE_ID,
                options["recovered_node"],
                destination_url=options["destination_url"],
            )
        except Exception as exc:
            raise CommandError(f"未完成受控回切；本站已保持只读：{exc}") from exc
        self.stdout.write(self.style.SUCCESS(
            f"写租约已交接至 {result['new_primary']}，任期 {result['epoch']}；新主站将通过轮询完成提升"
        ))
