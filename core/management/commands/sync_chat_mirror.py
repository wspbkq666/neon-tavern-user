from django.core.management.base import BaseCommand, CommandError

from core.chat_sync_client import sync_once
from core.user_directory_sync_client import sync_user_directory_once


class Command(BaseCommand):
    help = "将已同意同步的私聊事件通过 HTTPS 可靠发送至总站"

    def handle(self, *args, **options):
        results = []
        failures = []
        for label, sync in (("聊天", sync_once), ("账号目录", sync_user_directory_once)):
            try:
                result = sync()
                results.append((label, result))
                if result.get("error"):
                    failures.append(label)
            except Exception:
                failures.append(label)
                self.stderr.write(f"{label}同步异常；未确认数据仍保留在本地队列")
        by_target = {}
        for label, result in results:
            self.stdout.write(
                f"{label}: 发送 {result['sent']}，确认 {result['acknowledged']}，待发送 {result['pending']}"
            )
            for target in result["targets"]:
                row = by_target.setdefault(target["target_key"], {"sent": 0, "acknowledged": 0, "pending": 0, "errors": []})
                row["sent"] += target["sent"]
                row["acknowledged"] += target["acknowledged"]
                row["pending"] += target["pending"]
                if target["error"]:
                    row["errors"].append(target["error"])
        for target_key, target in sorted(by_target.items()):
            status = ",".join(sorted(set(target["errors"]))) or "正常"
            self.stdout.write(
                f"{target_key}: 发送 {target['sent']}，确认 {target['acknowledged']}，"
                f"待发送 {target['pending']}，状态 {status}"
            )
        if failures:
            raise CommandError("至少一类双站上报未完成；已成功部分保留确认结果，其他事件继续重试")
