from django.http import JsonResponse
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods
from .auth_api import body_or_error
from .character_api import authentication_error
from .models import ImportTask
from .smart_import_api import _extract_request_document
from .settings_api import effective_values,active_api_key
from .import_tasks import create_import_task,cancel_import_task,resume_import_task,configuration_matches


@require_http_methods(['GET','POST'])
def task_merge(request,task_id):
    from .import_merge import duplicate_groups,preview_merge
    from .smart_import import ITEM_KEYS,normalize_smart_import
    if error:=authentication_error(request): return error
    task=get_object_or_404(ImportTask,pk=task_id,owner=request.user,status='done')
    drafts=task.result.get('drafts',[])
    if request.method=='GET':
        return JsonResponse({'revision':task.revision,'groups':duplicate_groups(drafts)})
    data,error=body_or_error(request)
    if error: return error
    try:
        if set(data)-{'action','revision','indices','choices'} or type(data.get('revision')) is not int or data.get('action') not in ('preview','commit') or not isinstance(data.get('choices',{}),dict):
            raise ValueError('合并请求格式无效')
        if data['revision']!=task.revision: return JsonResponse({'error':'草稿已变化，请重新查看合并预览'},status=409)
        preview=preview_merge(drafts,data.get('indices'),data.get('choices',{}))
        if data['action']=='preview': return JsonResponse(preview)
        if any(row['selected'] is None for row in preview['conflicts']):
            raise ValueError('请逐项选择冲突内容；不会自动覆盖状态、分类或开场白')
        indices=set(data['indices']);first=min(indices)
        updated=[preview['draft'] if index==first else row for index,row in enumerate(drafts) if index==first or index not in indices]
        _,payload=normalize_smart_import({'items':[{key:row[key] for key in ITEM_KEYS} for row in updated]})
        result={**task.result,'drafts':updated,'payload':payload,'merge_history':[*task.result.get('merge_history',[]),{'originals':preview['originals'],'choices':data.get('choices',{})}]}
        changed=ImportTask.objects.filter(pk=task.pk,revision=task.revision,status='done').update(result=result,revision=F('revision')+1,updated_at=timezone.now())
        if not changed: return JsonResponse({'error':'草稿已变化，请重新查看'},status=409)
        task.refresh_from_db()
        return JsonResponse(task_payload(task,True))
    except (ValueError,KeyError,TypeError) as exc:
        return JsonResponse({'error':str(exc)},status=400)


def task_payload(task,detail=False):
    segments=list(task.segments.all())
    result={'id':str(task.pk),'filename':task.filename,'status':task.status,'revision':task.revision,'error':task.error,
        'configuration_changed':not configuration_matches(task),'created_at':task.created_at.isoformat(),'total':len(segments),'completed':sum(row.status=='done' for row in segments)}
    if detail:
        result.update(source=task.source,result=task.result,segments=[{'index':row.index,'start':row.start,'end':row.end,'status':row.status,'attempts':row.attempts,'error':row.error} for row in segments])
    return result


@require_http_methods(['GET','POST'])
def tasks(request):
    if error:=authentication_error(request):
        return error
    if request.method=='GET':
        return JsonResponse({'tasks':[task_payload(task) for task in ImportTask.objects.filter(owner=request.user).order_by('-created_at')[:100]]})
    extracted,error=_extract_request_document(request)
    if error:
        return error
    if not active_api_key(request.user):
        return JsonResponse({'error':'请先配置模型密钥，再提交识别任务'},status=400)
    task=create_import_task(request.user,extracted,effective_values(request.user))
    return JsonResponse(task_payload(task),status=202)


@require_http_methods(['GET','POST','PATCH'])
def task_detail(request,task_id):
    if error:=authentication_error(request):
        return error
    task=get_object_or_404(ImportTask,pk=task_id,owner=request.user)
    if request.method=='PATCH':
        from .smart_import import normalize_smart_import,ITEM_KEYS
        data,error=body_or_error(request)
        if error:
            return error
        try:
            if set(data)!={'revision','drafts','batch_name'} or not isinstance(data['drafts'],list) or not isinstance(data['batch_name'],str) or len(data['batch_name'])>120:
                raise ValueError('保存草稿的内容无效')
            items=[{key:draft[key] for key in ITEM_KEYS} for draft in data['drafts']]
            _,payload=normalize_smart_import({'items':items})
            result={**task.result,'drafts':data['drafts'],'payload':payload,'batch_name':data['batch_name']}
        except (KeyError,TypeError,ValueError) as exc:
            return JsonResponse({'error':'草稿格式无效，请先修正错误再保存'},status=400)
        changed=ImportTask.objects.filter(pk=task.pk,status='done',revision=data['revision']).update(result=result,revision=F('revision')+1,updated_at=timezone.now())
        if not changed:
            return JsonResponse({'error':'草稿已变化或任务尚未完成，请刷新后重试'},status=409)
        task.refresh_from_db()
        return JsonResponse(task_payload(task,True))
    if request.method=='POST':
        data,error=body_or_error(request)
        if error:
            return error
        if set(data)!={'action'} or data['action'] not in ('cancel','resume','restart'):
            return JsonResponse({'error':'任务操作无效'},status=400)
        try:
            if data['action']=='restart':
                from .smart_import_extract import ExtractedDocument
                if not active_api_key(request.user):
                    raise ValueError('请先配置当前模型的密钥')
                task=create_import_task(request.user,ExtractedDocument(task.filename,task.source,[]),effective_values(request.user))
                return JsonResponse(task_payload(task,True),status=202)
            task=cancel_import_task(request.user,task.pk) if data['action']=='cancel' else resume_import_task(request.user,task.pk)
        except ValueError as exc:
            return JsonResponse({'error':str(exc)},status=409)
        return JsonResponse(task_payload(task,True))
    try:
        cursor=int(request.GET.get('cursor','0'))
        if cursor<0:
            raise ValueError()
    except ValueError:
        return JsonResponse({'error':'日志游标无效'},status=400)
    events=list(task.events.filter(pk__gt=cursor).order_by('id')[:300])
    result=task_payload(task,True)
    result.update(events=[{'id':row.pk,**row.payload} for row in events],cursor=events[-1].pk if events else cursor,
        more_events=task.events.filter(pk__gt=events[-1].pk if events else cursor).exists())
    return JsonResponse(result,json_dumps_params={'ensure_ascii':False})


@require_http_methods(['GET','POST'])
def task_coverage(request,task_id):
    from .import_coverage import build_coverage_report,filter_source_with_records
    from .smart_import_extract import ExtractedDocument
    from .smart_import import normalize_smart_import,ITEM_KEYS
    if error:=authentication_error(request):
        return error
    task=get_object_or_404(ImportTask,pk=task_id,owner=request.user)
    cleaned,records=filter_source_with_records(task.source)
    report=build_coverage_report(task.source,task.result.get('drafts',[]),records)
    paragraphs={row['id']:row for row in report['paragraphs']}
    if request.method=='POST':
        data,error=body_or_error(request)
        if error:
            return error
        action=data.get('action')
        if task.status!='done' or data.get('revision')!=task.revision:
            return JsonResponse({'error':'任务尚未完成或草稿已变化，请刷新后重试'},status=409)
        try:
            with transaction.atomic():
                changed=ImportTask.objects.filter(pk=task.pk,status='done',revision=data['revision']).update(revision=F('revision')+1,updated_at=timezone.now())
                if not changed:
                    return JsonResponse({'error':'草稿已变化，请刷新'},status=409)
                if action=='review':
                    paragraph=paragraphs.get(data.get('paragraph_id'))
                    decision=data.get('decision')
                    if not paragraph or decision not in ('已核对','格式要求','需补充'):
                        raise ValueError('复核内容无效')
                    reviews={**task.coverage_reviews,paragraph['id']:decision}
                    ImportTask.objects.filter(pk=task.pk).update(coverage_reviews=reviews)
                elif action=='rescan':
                    paragraph=paragraphs.get(data.get('paragraph_id'))
                    if not paragraph:
                        raise ValueError('原文段落不存在')
                    child=create_import_task(request.user,ExtractedDocument(f'局部复核-{task.filename}'[:250],paragraph['text'],[]),effective_values(request.user))
                    child.parent=task
                    child.source_start=paragraph['start']
                    child.source_end=paragraph['end']
                    child.save(update_fields=['parent','source_start','source_end'])
                    return JsonResponse({'task_id':str(child.pk),'message':'已创建局部重识别任务；完成后需明确接受，原草稿不会被自动覆盖'},status=202)
                elif action=='accept_rescan':
                    child=get_object_or_404(ImportTask,pk=data.get('child_id'),owner=request.user,parent=task,status='done')
                    if task.source[child.source_start:child.source_end]!=child.source:
                        raise ValueError('原文来源已变化，不能接受旧建议')
                    drafts=task.result.get('drafts',[])+child.result.get('drafts',[])
                    items=[{key:draft[key] for key in ITEM_KEYS} for draft in drafts]
                    merged,payload=normalize_smart_import({'items':items})
                    result={**task.result,'drafts':[*(task.result.get('drafts',[])),*merged[len(task.result.get('drafts',[])):]],'payload':payload}
                    ImportTask.objects.filter(pk=task.pk).update(result=result)
                    # 将已接受的建议记录为复核项，避免重复接受。
                    accepted=f'建议:{child.pk}'
                    if accepted in task.coverage_reviews:
                        raise ValueError('该建议已经接受')
                    ImportTask.objects.filter(pk=task.pk).update(coverage_reviews={**task.coverage_reviews,accepted:'已接受，新增到草稿；同名项需人工确认'})
                else:
                    raise ValueError('复核操作无效')
        except (ValueError,KeyError,TypeError) as exc:
            return JsonResponse({'error':str(exc)},status=400)
        task.refresh_from_db()
    for row in report['paragraphs']:
        row['decision']=task.coverage_reviews.get(row['id'],'')
    report.update(revision=task.revision,cleaned_source=cleaned,reviews=task.coverage_reviews,
        rescans=[{**task_payload(child),'source_start':child.source_start,'source_end':child.source_end} for child in task.rescans.all()])
    return JsonResponse(report,json_dumps_params={'ensure_ascii':False})
