import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections,transaction
from django.db.models import Q
from django.utils import timezone

from core.generation import process_job
from core.models import GenerationJob,Conversation
from core.import_tasks import claim_import_segment,process_import_segment
from core.personal_backup_jobs import claim_backup_job,process_backup_job
import uuid


class Command(BaseCommand):
    help = "Process queued Neon Tavern generation jobs"

    def handle(self, *args, **options):
        worker_id=f'本地进程-{uuid.uuid4().hex}'
        while True:
            close_old_connections()
            now=timezone.now()
            with transaction.atomic():
                stale=GenerationJob.objects.filter(Q(lease_until__lt=now)|Q(lease_until__isnull=True),status='running')
                stale.update(status='failed',error='生成服务租约过期，请重试',finished_at=now)
                memories=GenerationJob.objects.filter(status='failed',finished_at=now,task_kind='memory').values('conversation_id')
                Conversation.objects.filter(pk__in=memories,memory_summary_status='running').update(memory_summary_status='failed',memory_summary_error='总结服务中断，请重新总结')
            imported=False
            segment=claim_import_segment(worker_id,timezone.now())
            if segment:
                process_import_segment(segment)
                imported=True
            job_id = GenerationJob.objects.filter(status="queued").order_by("created_at").values_list("id", flat=True).first()
            if job_id:
                try:
                    process_job(job_id)
                except Exception:
                    GenerationJob.objects.filter(pk=job_id,status__in=['queued','running']).update(
                        status="failed", error="生成服务发生错误，请稍后重试", finished_at=timezone.now()
                    )
            backup=claim_backup_job()
            if backup:
                process_backup_job(backup)
            elif not imported and not job_id:
                time.sleep(1)
