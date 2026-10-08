"""导入分段使用可续租令牌，所有结果写入都重新检查任务状态。"""
import hashlib
import json
import uuid
from datetime import timedelta
from threading import Event,Thread
from django.db import transaction,close_old_connections,OperationalError
from django.db.models import Q,F
from django.utils import timezone
from .models import ImportTask,ImportSegment,ImportTaskEvent,UserSettings
from .smart_import import split_document,analyze_document,normalize_smart_import
from .settings_api import active_api_key,effective_values

OPTION_FIELDS={'provider_id','api_base_url','model','temperature','top_p','max_tokens','context_capacity'}
LEASE_SECONDS=180


def configuration_matches(task):
    current=effective_values(task.owner)
    return all(current.get(key)==value for key,value in task.options.items() if key in OPTION_FIELDS)


def create_import_task(owner,extracted,options):
    chunks=split_document(extracted.text)
    with transaction.atomic():
        task=ImportTask.objects.create(owner=owner,filename=extracted.filename[:250],source=extracted.text,
            source_hash=hashlib.sha256(extracted.text.encode()).hexdigest(),options={key:value for key,value in options.items() if key in OPTION_FIELDS})
        start=0
        for index,chunk in enumerate(chunks):
            offset=extracted.text.find(chunk,start)
            if offset<0:
                raise ValueError('无法定位原文分段')
            ImportSegment.objects.create(task=task,index=index,start=offset,end=offset+len(chunk))
            start=offset+1
        ImportTaskEvent.objects.create(task=task,payload={'type':'progress','stage':'document_ready','characters':len(extracted.text),'warnings':extracted.warnings})
    return task


def claim_import_segment(worker_id,now):
    try:
        return _claim_import_segment(worker_id,now)
    except OperationalError as exc:
        if 'locked' not in str(exc).lower():
            raise
        return None


def _claim_import_segment(worker_id,now):
    available=Q(status='queued')|Q(status='running',lease_until__lt=now)
    for candidate in ImportSegment.objects.filter(available,task__status__in=['queued','running']).order_by('task__created_at','index')[:20]:
        with transaction.atomic():
            token=uuid.uuid4().hex
            changed=ImportSegment.objects.filter(available,pk=candidate.pk,task__status__in=['queued','running']).update(
                status='running',lease_token=token,lease_until=now+timedelta(seconds=LEASE_SECONDS),worker_id=worker_id[:120],attempts=F('attempts')+1,error='')
            if changed:
                ImportTask.objects.filter(pk=candidate.task_id,status__in=['queued','running']).update(status='running',updated_at=now)
                candidate.refresh_from_db()
                return candidate
    return None


def heartbeat(segment_id,token):
    return ImportSegment.objects.filter(pk=segment_id,status='running',lease_token=token,lease_until__gte=timezone.now(),task__status='running').update(lease_until=timezone.now()+timedelta(seconds=LEASE_SECONDS))==1


def append_event(segment_id,token,event):
    with transaction.atomic():
        if not heartbeat(segment_id,token):
            raise ValueError('任务已取消或租约已被收回')
        segment=ImportSegment.objects.get(pk=segment_id)
        if event.get('type')=='raw_delta':
            raw=segment.raw_output+event.get('text','')
            if len(raw)>2000000:
                raise ValueError('原始回复超过日志保存上限，已保留此前内容')
            ImportSegment.objects.filter(pk=segment_id,lease_token=token).update(raw_output=raw)
        ImportTaskEvent.objects.create(task_id=segment.task_id,payload={**event,'segment_index':segment.index})


def complete_import_segment(segment_id,lease_token,result):
    # 先验证单段格式；失败不得标为完成。
    normalize_smart_import(result)
    with transaction.atomic():
        changed=ImportSegment.objects.filter(pk=segment_id,status='running',lease_token=lease_token,lease_until__gte=timezone.now(),task__status='running').update(status='done',result=result,lease_token='',lease_until=None)
        if not changed:
            return False
        segment=ImportSegment.objects.get(pk=segment_id)
        task=ImportTask.objects.get(pk=segment.task_id)
        ImportTaskEvent.objects.create(task=task,payload={'type':'segment_complete','segment_index':segment.index})
        if task.segments.exclude(status='done').exists():
            return True
        items=[]
        seen=set()
        for part in task.segments.all():
            for item in part.result.get('items',[]):
                signature=json.dumps(item,ensure_ascii=False,sort_keys=True)
                if signature not in seen:
                    seen.add(signature)
                    items.append(item)
        names={}
        for item in items:
            key=(item.get('type'),str(item.get('fields',{}).get('name','')).casefold())
            names.setdefault(key,[]).append(item)
        for matches in names.values():
            if len(matches)>1:
                for item in matches:
                    item['warnings']=list(dict.fromkeys([*item.get('warnings',[]),'分段中有同名但内容不同的草稿，请确认是否属于同一项；未自动合并。']))
        try:
            drafts,payload=normalize_smart_import({'items':items})
            result={'drafts':drafts,'payload':payload,'warnings':list(dict.fromkeys(warning for draft in drafts for warning in draft['warnings']))}
        except (ValueError,TypeError) as exc:
            task.status='failed'
            task.error='合并结果未通过格式校验，分段结果和日志已保留'
            task.save(update_fields=['status','error','updated_at'])
            ImportTaskEvent.objects.create(task=task,payload={'type':'error','message':task.error})
            return True
        task.status='done'
        task.result=result
        task.error=''
        task.save(update_fields=['status','result','error','updated_at'])
        ImportTaskEvent.objects.create(task=task,payload={'type':'complete'})
    return True


def owned_task(owner,task_id):
    task=ImportTask.objects.filter(pk=task_id,owner=owner).first()
    if not task:
        raise ValueError('导入任务不存在或不属于当前账号')
    return task


def cancel_import_task(owner,task_id):
    with transaction.atomic():
        task=owned_task(owner,task_id)
        changed=ImportTask.objects.filter(pk=task.pk,status__in=['queued','running','failed']).update(status='canceled',revision=F('revision')+1,updated_at=timezone.now())
        if changed:
            task.segments.exclude(status='done').update(status='canceled',lease_token='',lease_until=None)
            ImportTaskEvent.objects.create(task=task,payload={'type':'canceled','message':'导入已取消，已完成的分段保留'})
    return owned_task(owner,task_id)


def resume_import_task(owner,task_id):
    with transaction.atomic():
        task=owned_task(owner,task_id)
        if not configuration_matches(task):
            raise ValueError('模型配置已变化。请恢复原配置后继续，或使用当前配置重新识别；原结果仍保留')
        changed=ImportTask.objects.filter(pk=task.pk,status__in=['canceled','failed']).update(status='queued',error='',revision=F('revision')+1,updated_at=timezone.now())
        if not changed:
            raise ValueError('仅取消或失败的任务可以继续')
        task.segments.exclude(status='done').update(status='queued',lease_token='',lease_until=None,error='')
        if not task.segments.exclude(status='done').exists():
            # 合并失败时显式重试最后一段，避免显示排队但永远没有可领取任务。
            last=task.segments.order_by('-index').first()
            if last:
                last.status='queued'
                last.save(update_fields=['status'])
        ImportTaskEvent.objects.create(task=task,payload={'type':'resumed','message':'正在继续未完成的分段'})
    return owned_task(owner,task_id)


def process_import_segment(segment):
    token=segment.lease_token
    stop=Event()
    def renew():
        close_old_connections()
        try:
            while not stop.wait(30):
                if not heartbeat(segment.pk,token):
                    break
        finally:
            close_old_connections()
    renewal=Thread(target=renew,daemon=True)
    renewal.start()
    try:
        task=ImportTask.objects.select_related('owner').get(pk=segment.task_id)
        with transaction.atomic():
            UserSettings.objects.get_or_create(user=task.owner)
            UserSettings.objects.select_for_update().get(user=task.owner)
            if not configuration_matches(task):
                raise ValueError('模型配置已变化，已停止请求，防止密钥发送到旧服务')
            key=active_api_key(task.owner)
        if not key:
            raise ValueError('没有可用的模型密钥')
        source=task.source[segment.start:segment.end]
        result=analyze_document(source,options=task.options,api_key=key,
            on_progress=lambda event:append_event(segment.pk,token,{'type':'progress',**event}),
            on_raw_chunk=lambda text:append_event(segment.pk,token,{'type':'raw_delta','text':text}))
        complete_import_segment(segment.pk,token,result)
    except Exception:
        with transaction.atomic():
            changed=ImportSegment.objects.filter(pk=segment.pk,status='running',lease_token=token,task__status='running').update(
                status='failed',error='分段识别失败，请检查模型设置后继续',lease_token='',lease_until=None)
            if changed:
                ImportTask.objects.filter(pk=segment.task_id,status='running').update(status='failed',error='部分分段识别失败，已保存成功结果',updated_at=timezone.now())
                ImportTaskEvent.objects.create(task_id=segment.task_id,payload={'type':'error','message':'分段识别失败，已完成内容与原始回复仍保留','segment_index':segment.index})
    finally:
        stop.set()
        renewal.join(timeout=2)
