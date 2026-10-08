import uuid
from datetime import timedelta
from threading import Event,Thread
from django.db import OperationalError,close_old_connections,transaction
from django.db.models import Q
from django.utils import timezone
from .models import PersonalBackupJob
from .personal_backup import serialize_personal_backup,validate_personal_backup,restore_personal_backup


def claim_backup_job():
    now=timezone.now()
    PersonalBackupJob.objects.filter(created_at__lt=now-timedelta(days=7)).delete()
    ready=Q(status='queued')|Q(status='running',lease_until__lt=now)
    try:
        for job in PersonalBackupJob.objects.filter(ready).order_by('created_at')[:20]:
            token=uuid.uuid4().hex
            if PersonalBackupJob.objects.filter(ready,pk=job.pk).update(status='running',lease_token=token,lease_until=now+timedelta(seconds=180)):
                job.refresh_from_db()
                return job
    except OperationalError as exc:
        if 'locked' not in str(exc).lower():
            raise
    return None


def process_backup_job(job):
    token=job.lease_token
    stop=Event()
    def renew():
        close_old_connections()
        try:
            while not stop.wait(30):
                try:
                    changed=PersonalBackupJob.objects.filter(pk=job.pk,status='running',lease_token=token,lease_until__gte=timezone.now()).update(lease_until=timezone.now()+timedelta(seconds=180))
                    if not changed:
                        break
                except OperationalError:
                    continue
        finally:
            close_old_connections()
    renewal=Thread(target=renew,daemon=True)
    renewal.start()
    try:
        if job.kind=='export':
            package=serialize_personal_backup(job.owner)
            result={'bytes':len(package),'message':'个人备份已生成'}
        else:
            package=bytes(job.package)
            preview=validate_personal_backup(job.owner,package)
            if job.kind=='preview':
                result={'counts':preview['counts'],'warnings':preview['warnings'],'package_hash':preview['package_hash'],'bytes':len(package)}
            elif job.kind=='restore':
                result=restore_personal_backup(job.owner,preview,f'后台恢复:{job.result["preview_id"]}')
            else:
                raise ValueError('备份任务类型无效')
        PersonalBackupJob.objects.filter(pk=job.pk,status='running',lease_token=token,lease_until__gte=timezone.now()).update(status='done',result=result,package=package,error='',lease_token='',lease_until=None)
    except Exception as exc:
        message=str(exc)[:300] if isinstance(exc,ValueError) else '备份处理失败，未完成的恢复已回滚，请重试'
        PersonalBackupJob.objects.filter(pk=job.pk,status='running',lease_token=token,lease_until__gte=timezone.now()).update(status='failed',error=message,lease_token='',lease_until=None)
    finally:
        stop.set()
        renewal.join(timeout=2)
