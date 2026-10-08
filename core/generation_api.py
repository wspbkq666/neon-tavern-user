import json

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .auth_api import body_or_error
from .character_api import authentication_error
from .ai_text_filter import sanitize_ai_value
from .generation import call_json_model
from .models import Conversation, GenerationJob, Message
from .settings_api import active_api_key, effective_values


def npc_ids(conversation):
    return [
        str(link.character_id)
        for link in conversation.conversationparticipant_set.select_related("character").order_by("position")
        if not link.character.is_player_controlled
    ]


def queue_generation(conversation, user, actor_ids, mode="serial", hints=None, auto=False):
    if not isinstance(actor_ids, list) or not actor_ids or len(actor_ids) > 12 or len(actor_ids) != len(set(map(str, actor_ids))):
        raise ValueError("请选择至少一名 NPC")
    allowed = set(npc_ids(conversation))
    if any(not isinstance(actor_id, str) or actor_id not in allowed for actor_id in actor_ids):
        raise ValueError("只能选择在场的 NPC")
    if mode not in {"serial", "parallel"}:
        raise ValueError("生成方式无效")
    if not isinstance(hints, dict) or len(hints) > 12 or any(
        key not in allowed or not isinstance(value, str) or len(value) > 500 for key, value in hints.items()
    ):
        raise ValueError("导演提示无效")
    with transaction.atomic():
        queued = GenerationJob.objects.filter(conversation=conversation, status="queued").first()
        running = GenerationJob.objects.filter(conversation=conversation, status="running").exists()
        if auto and queued:
            return queued
        if not auto and (queued or running):
            return None
        if GenerationJob.objects.filter(requested_by=user, status__in=["queued", "running"]).count() >= 3:
            raise ValueError("等待中的生成任务过多，请稍后再试")
        try:
            with transaction.atomic():
                return GenerationJob.objects.create(
                    conversation=conversation, requested_by=user, actor_ids=actor_ids,
                    mode=mode, director_hints=hints, auto=auto,
                    story_revision=Conversation.objects.get(pk=conversation.pk).story_revision,
                )
        except IntegrityError:
            return GenerationJob.objects.filter(conversation=conversation, status="queued").first() if auto else None


@require_POST
def generate(request, conversation_id):
    error = authentication_error(request)
    if error:
        return error
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    data, error = body_or_error(request)
    if error:
        return error
    try:
        job = queue_generation(
            conversation, request.user, data.get("actor_ids"), data.get("mode", "serial"), data.get("director_hints", {}),
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if job is None:
        return JsonResponse({"error": "已有生成任务正在进行"}, status=409)
    return JsonResponse({"job_id": str(job.id), "status": job.status}, status=202)


@require_GET
def job_detail(request, job_id):
    error = authentication_error(request)
    if error:
        return error
    job = get_object_or_404(GenerationJob, pk=job_id, requested_by=request.user, conversation__owner=request.user)
    return JsonResponse({
        "id": str(job.id), "conversation_id": str(job.conversation_id), "status": job.status,
        "progress": job.progress, "error": job.error, "actor_ids": job.actor_ids,
        "created_at": job.created_at.isoformat(),
    })


@require_POST
def cancel(request, job_id):
    error = authentication_error(request)
    if error:
        return error
    job = get_object_or_404(GenerationJob, pk=job_id, requested_by=request.user, conversation__owner=request.user)
    if job.status not in {'queued','running'}:
        return JsonResponse({"error": "生成任务已经结束"}, status=409)
    job.status = "cancelled"
    job.error = "用户取消了本轮生成"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    if job.task_kind=='memory':
        from django.db.models import F
        Conversation.objects.filter(pk=job.conversation_id).update(story_revision=F('story_revision')+1,memory_summary_status='idle',memory_summary_error='已取消本次总结')
    return JsonResponse({"id": str(job.id), "status": job.status})


@require_POST
def retry(request, job_id):
    error = authentication_error(request)
    if error:
        return error
    job = get_object_or_404(GenerationJob, pk=job_id, requested_by=request.user, conversation__owner=request.user)
    if job.status not in {"failed", "done"}:
        return JsonResponse({"error": "当前生成仍在进行"}, status=409)
    try:
        replacement = queue_generation(job.conversation, request.user, job.actor_ids, job.mode, job.director_hints)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if replacement is None:
        return JsonResponse({"error": "已有生成任务正在进行"}, status=409)
    return JsonResponse({"job_id": str(replacement.id), "status": replacement.status}, status=202)


@require_POST
def suggest(request, conversation_id):
    error = authentication_error(request)
    if error:
        return error
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    data, error = body_or_error(request)
    if error:
        return error
    if set(data) - {"force_router"} or ("force_router" in data and not isinstance(data["force_router"], bool)):
        return JsonResponse({"error": "建议请求内容无效"}, status=400)
    force_router = data.get("force_router", False)
    links = list(conversation.conversationparticipant_set.select_related("character").order_by("position"))
    npcs = [link.character for link in links if not link.character.is_player_controlled]
    if not npcs:
        return JsonResponse({"actor_ids": [], "director_hints": {}, "source": "rules"})
    latest = list(conversation.messages.select_related("speaker").order_by("-created_at", "-id")[:15])
    turn = []
    for item in latest:
        if item.source == "ai":
            break
        if item.source == "user":
            turn.append(item)
    recent_text = " ".join(item.content for item in turn[:5])[:500]
    mentioned = [npc for npc in npcs if f"@{npc.name}" in recent_text or f"＠{npc.name}" in recent_text]
    if mentioned and not force_router:
        return JsonResponse({"actor_ids": [str(npc.id) for npc in mentioned], "director_hints": {}, "source": "rules"})
    latest_ai = next((item.speaker_id for item in latest if item.source == "ai" and item.speaker_id), None)
    scores = {}
    for npc in npcs:
        state = " ".join(str(value) for value in npc.state_fields.values())
        scores[str(npc.id)] = (2 if "话多" in state or "主动" in state else 0) - (2 if "沉默" in state else 0) - (1 if npc.id == latest_ai else 0)
    maximum = max(scores.values())
    winners = [actor_id for actor_id, score in scores.items() if score == maximum]
    if len(winners) == 1 and not force_router:
        return JsonResponse({"actor_ids": winners, "director_hints": {}, "source": "rules"})
    key = active_api_key(request.user)
    if key:
        options = effective_values(request.user)
        try:
            result = call_json_model(
                [{"role": "system", "content": "你是 Router Agent。根据最近对话、当前场景和在场角色，按实际行动先后生成 NPC 行动队列。仅返回 JSON 对象，格式 {\"action_queue\":[\"角色ID\"],\"director_hints\":{\"角色ID\":\"一句提示\"}}。不要选择玩家角色。"},
                 {"role": "user", "content": json.dumps(sanitize_ai_value({"npcs": [{"id": str(npc.id), "name": npc.name, "summary": npc.summary, "personality": npc.personality, "state": npc.state_fields} for npc in npcs], "recent": [{"speaker": item.speaker.name if item.speaker else "场景", "kind": item.kind, "content": item.content} for item in reversed(latest)], "environment": conversation.environment})[0], ensure_ascii=False)}],
                options, key, max_tokens=400,
            )
            selected = [actor_id for actor_id in result.get("action_queue", result.get("should_reply", [])) if actor_id in scores]
            hints = result.get("director_hints", {})
            if selected and isinstance(hints, dict):
                hints = {key: value[:500] for key, value in hints.items() if key in scores and isinstance(value, str)}
                return JsonResponse({"actor_ids": selected, "director_hints": hints, "source": "model"})
        except (ValueError, RuntimeError):
            pass
    return JsonResponse({"actor_ids": [winners[0]], "director_hints": {}, "source": "rules_fallback"})
