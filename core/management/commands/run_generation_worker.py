import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from core.generation import process_job
from core.models import GenerationJob


class Command(BaseCommand):
    help = "Process queued Neon Tavern generation jobs"

    def handle(self, *args, **options):
        GenerationJob.objects.filter(status="running").update(
            status="failed", error="生成服务中断，请重新生成", finished_at=timezone.now()
        )
        while True:
            close_old_connections()
            job_id = GenerationJob.objects.filter(status="queued").order_by("created_at").values_list("id", flat=True).first()
            if job_id:
                try:
                    process_job(job_id)
                except Exception:
                    GenerationJob.objects.filter(pk=job_id).update(
                        status="failed", error="生成服务发生错误，请稍后重试", finished_at=timezone.now()
                    )
            else:
                time.sleep(1)
