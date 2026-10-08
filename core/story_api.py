from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .auth_api import body_or_error
from .character_api import authentication_error
from .models import Conversation, GenerationJob, StoryCheckpoint
from .story_snapshots import create_checkpoint, serialize_story, restore_checkpoint, fork_checkpoint
from .story_memory import update_memory, memory_payload
from django.db import transaction
from .story_state import (StoryConflict, ensure_story_state, state_payload, template_snapshot,
                          update_story_state, apply_template_update)


@require_http_methods(['GET','PATCH','POST'])
def actor_state(request,conversation_id,character_id):
    if error:=authentication_error(request):
        return error
    conversation=get_object_or_404(Conversation,pk=conversation_id,owner=request.user)
    character=get_object_or_404(conversation.participants,pk=character_id,owner=request.user)
    state=ensure_story_state(conversation,character)
    if request.method=='GET':
        current=template_snapshot(character)
        return JsonResponse({**state_payload(state),'template_changes':{
            key:{'before':state.template_snapshot.get(key),'after':value}
            for key,value in current.items() if value!=state.template_snapshot.get(key)}})
    data,error=body_or_error(request)
    if error:
        return error
    try:
        if request.method=='POST':
            if set(data)-{'revision','reset_state','selected_fields'} or type(data.get('reset_state',False)) is not bool:
                raise ValueError('角色卡更新选项无效')
            state=apply_template_update(conversation,character,data.get('revision'),reset_state=data.get('reset_state',False),selected_fields=data.get('selected_fields'))
        else:
            revision=data.pop('revision',None)
            state=update_story_state(conversation,character,data,revision)
    except StoryConflict as exc:
        return JsonResponse({'error':str(exc)},status=409)
    except (ValueError,TypeError) as exc:
        return JsonResponse({'error':str(exc)},status=400)
    return JsonResponse(state_payload(state))


@require_http_methods(['GET','PATCH','POST'])
def memory(request,conversation_id):
    if error:=authentication_error(request):
        return error
    conversation=get_object_or_404(Conversation,pk=conversation_id,owner=request.user)
    if request.method=='GET':
        return JsonResponse(memory_payload(conversation))
    data,error=body_or_error(request)
    if error:
        return error
    try:
        if request.method=='PATCH':
            if set(data)!={'revision','text','locked_facts'}:
                raise ValueError('记忆编辑内容无效')
            update_memory(conversation,data['text'],data['locked_facts'],data['revision'])
        else:
            from .settings_api import active_api_key
            if not active_api_key(request.user):
                raise ValueError('请先配置可用的模型 API 密钥')
            if set(data)!={'revision'} or type(data['revision']) is not int:
                raise ValueError('记忆重试版本无效')
            with transaction.atomic():
                if not Conversation.objects.filter(pk=conversation.pk,memory_revision=data['revision']).update(memory_summary_status='queued',memory_summary_error=''):
                    raise StoryConflict('记忆已变化，请重新读取')
                if conversation.generation_jobs.filter(status__in=['queued','running']).exists():
                    raise StoryConflict('已有任务正在进行，请稍后重试')
                GenerationJob.objects.create(conversation=conversation,requested_by=request.user,task_kind='memory',
                    actor_ids=[],story_revision=conversation.story_revision)
    except StoryConflict as exc:
        return JsonResponse({'error':str(exc)},status=409)
    except (ValueError,TypeError) as exc:
        return JsonResponse({'error':str(exc)},status=400)
    return JsonResponse(memory_payload(conversation))


def checkpoint_payload(checkpoint):
    return {'id':str(checkpoint.pk),'name':checkpoint.name,'created_at':checkpoint.created_at.isoformat(),
            'message_count':len(checkpoint.snapshot.get('messages',[]))}


@require_http_methods(['GET','POST'])
def checkpoints(request,conversation_id):
    if error:=authentication_error(request):
        return error
    conversation=get_object_or_404(Conversation,pk=conversation_id,owner=request.user)
    if request.method=='GET':
        return JsonResponse({'items':[checkpoint_payload(item) for item in conversation.checkpoints.all()]})
    data,error=body_or_error(request)
    if error:
        return error
    try:
        if set(data)-{'name','through_message_id'}:
            raise ValueError('存档选项无效')
        checkpoint=create_checkpoint(conversation,data.get('name'),data.get('through_message_id'))
    except (ValueError,TypeError) as exc:
        return JsonResponse({'error':str(exc)},status=400)
    return JsonResponse(checkpoint_payload(checkpoint),status=201)


@require_http_methods(['GET','POST'])
def checkpoint_detail(request,conversation_id,checkpoint_id):
    if error:=authentication_error(request):
        return error
    conversation=get_object_or_404(Conversation,pk=conversation_id,owner=request.user)
    checkpoint=get_object_or_404(StoryCheckpoint,pk=checkpoint_id,conversation=conversation)
    if request.method=='GET':
        current=serialize_story(conversation)
        target=checkpoint.snapshot
        changes={}
        labels={'environment':'环境','long_memory':'事件记忆','locked_facts':'锁定事实','actors':'角色状态与设定',
                'worldbook_selection':'世界书选择','worldbooks':'世界书内容'}
        for key,label in labels.items():
            if current.get(key)!=target.get(key):
                changes[label]={'before':current.get(key),'after':target.get(key)}
        return JsonResponse({**checkpoint_payload(checkpoint),'revision':conversation.story_revision,
            'current_message_count':len(current['messages']),'changes':changes})
    data,error=body_or_error(request)
    if error:
        return error
    try:
        if data.get('action')=='fork' and set(data)=={'action','title'}:
            branch=fork_checkpoint(request.user,checkpoint,data['title'])
            return JsonResponse({'conversation_id':str(branch.pk)},status=201)
        if data.get('action')=='restore' and set(data)=={'action','revision'}:
            return JsonResponse(restore_checkpoint(conversation,checkpoint,data['revision']))
        raise ValueError('请选择有效的分支或回档操作')
    except StoryConflict as exc:
        return JsonResponse({'error':str(exc)},status=409)
    except (ValueError,TypeError) as exc:
        return JsonResponse({'error':str(exc)},status=400)
