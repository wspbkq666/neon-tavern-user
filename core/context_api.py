import hashlib
import json
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .auth_api import body_or_error
from .character_api import authentication_error
from .context_selection import character_segments,validate_context_policy
from .models import Character


def payload(actor):
    revision=hashlib.sha256(json.dumps({'personality':actor.personality,'memories':actor.memories,'policy':actor.context_policy,'updated_at':actor.updated_at.isoformat()},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'revision':revision,'policy':actor.context_policy,'segments':character_segments(actor),'estimated':True}


@require_http_methods(['GET','PATCH'])
def character_context(request,character_id):
    error=authentication_error(request)
    if error:
        return error
    actor=get_object_or_404(Character,pk=character_id,owner=request.user)
    if request.method=='GET':
        return JsonResponse(payload(actor))
    data,error=body_or_error(request)
    if error:
        return error
    try:
        if set(data)!={'revision','policy'}:
            raise ValueError('核心确认内容无效')
        validate_context_policy(data['policy'])
        known={row['id'] for row in character_segments(actor)}
        if set(data['policy'].get('segments',{}))-known:
            raise ValueError('原文段落已发生变化，请刷新后重新确认')
        if data['policy'].get('confirmed') and not data['policy'].get('core_text','').strip():
            raise ValueError('请填写核心设定后确认')
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=400)
    with transaction.atomic():
        if data['revision']!=payload(actor)['revision']:
            return JsonResponse({'error':'原文或核心设定已更新，请刷新后重试'},status=409)
        changed=Character.objects.filter(pk=actor.pk,owner=request.user,updated_at=actor.updated_at).update(context_policy=data['policy'],updated_at=timezone.now())
        if not changed:
            return JsonResponse({'error':'原文或核心设定已更新，请刷新后重试'},status=409)
        actor.refresh_from_db()
    return JsonResponse(payload(actor))
