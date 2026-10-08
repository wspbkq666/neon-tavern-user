import uuid

from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .auth_api import body_or_error
from .character_api import authentication_error, character_payload
from .generation_api import npc_ids, queue_generation
from .models import Character, Conversation, ConversationParticipant, GenerationJob, Message
from .story_state import ensure_story_state, actor_context, state_payload


def message_payload(message):
    return {
        "id": str(message.id),
        "speaker_id": str(message.speaker_id) if message.speaker_id else None,
        "kind": message.kind,
        "content": message.content,
        "source": message.source,
        "consumed_by": message.consumed_by,
        "state_snapshot": message.state_snapshot,
        "has_story_snapshot": bool(message.story_snapshot),
        "context_trace_id": str(message.context_trace_id) if message.context_trace_id else None,
        "created_at": message.created_at.isoformat(),
    }


def conversation_payload(conversation, *, include_messages=False):
    participant_links = conversation.conversationparticipant_set.select_related("character").order_by("position")
    result = {
        "id": str(conversation.id),
        "title": conversation.title,
        "environment": conversation.environment,
        "language": conversation.language,
        "player_character_id": str(conversation.player_character_id),
        "participants": [{**character_payload(actor_context(conversation,link.character)),
                          'story_state':state_payload(ensure_story_state(conversation,link.character))}
                         for link in participant_links],
        "story_revision": conversation.story_revision,
        "auto_generate": conversation.auto_generate,
        "auto_actor_ids": conversation.auto_actor_ids,
        "auto_mode": conversation.auto_mode,
        "updated_at": conversation.updated_at.isoformat(),
    }
    if include_messages:
        result["messages"] = [message_payload(message) for message in conversation.messages.all()]
        result["long_memory"] = conversation.long_memory
    return result


def valid_uuid_list(value):
    if not isinstance(value, list) or not value or len(value) > 12 or not all(isinstance(item, str) for item in value):
        return False
    try:
        return len({uuid.UUID(item) for item in value}) == len(value)
    except (ValueError, AttributeError, TypeError):
        return False


def valid_environment(value):
    return (
        isinstance(value, dict) and len(value) <= 20
        and all(isinstance(key, str) and 1 <= len(key) <= 50 and isinstance(item, str) and len(item) <= 2000 for key, item in value.items())
    )


def validate_conversation_data(data, *, creation=False):
    allowed = {"title", "environment", "language", "auto_generate", "auto_actor_ids", "auto_mode", "character_ids", "player_character_id", "opening_greetings"}
    if any(key not in allowed for key in data):
        return False
    if creation and ("title" not in data or "player_character_id" not in data or "character_ids" not in data):
        return False
    if "title" in data and (not isinstance(data["title"], str) or not 1 <= len(data["title"].strip()) <= 120):
        return False
    if "environment" in data and not valid_environment(data["environment"]):
        return False
    if "language" in data and (not isinstance(data["language"], str) or not 1 <= len(data["language"]) <= 32):
        return False
    if "auto_generate" in data and not isinstance(data["auto_generate"], bool):
        return False
    if "auto_mode" in data and data["auto_mode"] not in {"serial", "parallel"}:
        return False
    if "character_ids" in data and not valid_uuid_list(data["character_ids"]):
        return False
    if "auto_actor_ids" in data and (not isinstance(data["auto_actor_ids"], list) or len(data["auto_actor_ids"]) > 12 or not all(isinstance(item, str) for item in data["auto_actor_ids"])):
        return False
    if "player_character_id" in data:
        try:
            uuid.UUID(data["player_character_id"])
        except (ValueError, AttributeError, TypeError):
            return False
    return True


@require_http_methods(["GET", "POST"])
def conversations(request):
    error = authentication_error(request)
    if error:
        return error
    if request.method == "GET":
        return JsonResponse(
            {"conversations": [conversation_payload(item) for item in Conversation.objects.filter(owner=request.user)]}
        )

    data, error = body_or_error(request)
    if error:
        return error
    if not validate_conversation_data(data, creation=True):
        return JsonResponse({"error": "对话内容无效"}, status=400)

    title = data["title"].strip()
    environment = data.get("environment", {})
    character_ids = data["character_ids"]
    player = Character.objects.filter(pk=data.get("player_character_id"), owner=request.user, is_player_controlled=True).first()
    npcs = list(Character.objects.filter(id__in=character_ids, owner=request.user, is_player_controlled=False))
    if not player or not npcs or len(npcs) != len(character_ids):
        return JsonResponse({"error": "请选择自己的玩家角色卡和至少一名 NPC"}, status=400)
    auto_ids = data.get("auto_actor_ids", [])
    greetings=data.get('opening_greetings',{})
    opening=[]
    if not isinstance(greetings,dict) or len(greetings)>12 or set(greetings)-{str(npc.pk) for npc in npcs}:
        return JsonResponse({'error':'开场白角色选择无效'},status=400)
    for npc in npcs:
        if str(npc.pk) not in greetings:
            continue
        index=greetings[str(npc.pk)]
        if type(index) is not int or index < -1 or index >= len(npc.alternate_greetings):
            return JsonResponse({'error':'开场白编号无效'},status=400)
        text=npc.first_mes if index==-1 else npc.alternate_greetings[index]
        if not text:
            return JsonResponse({'error':'所选开场白没有原文内容'},status=400)
        opening.append((npc,text))
    if len(auto_ids) != len(set(auto_ids)) or any(actor_id not in {str(npc.id) for npc in npcs} for actor_id in auto_ids):
        return JsonResponse({"error": "自动回复角色无效"}, status=400)

    with transaction.atomic():
        conversation = Conversation.objects.create(
            owner=request.user,
            title=title,
            environment=environment,
            player_character=player,
            language=data.get("language", "中文"),
            auto_generate=data.get("auto_generate") is True,
            auto_actor_ids=auto_ids,
            auto_mode="serial",
        )
        ConversationParticipant.objects.create(conversation=conversation, character=player, position=0)
        for position, npc in enumerate(npcs, start=1):
            ConversationParticipant.objects.create(conversation=conversation, character=npc, position=position)
        for character in [player,*npcs]:
            ensure_story_state(conversation,character,recover_history=False)
        for npc,text in opening:
            Message.objects.create(conversation=conversation,speaker=npc,kind=Message.DIALOGUE,content=text,source='preset')
    return JsonResponse(conversation_payload(conversation), status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
def conversation_detail(request, conversation_id):
    error = authentication_error(request)
    if error:
        return error
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    if request.method == "DELETE":
        if conversation.generation_jobs.filter(status__in=["queued", "running"]).exists():
            return JsonResponse({"error": "请等待当前生成完成"}, status=409)
        conversation.delete()
        return JsonResponse({}, status=204)
    if request.method == "PATCH":
        data, error = body_or_error(request)
        if error:
            return error
        if not data or not validate_conversation_data(data) or "player_character_id" in data or 'opening_greetings' in data:
            return JsonResponse({"error": "对话设置无效"}, status=400)
        npcs = None
        if "character_ids" in data:
            npcs = list(Character.objects.filter(id__in=data["character_ids"], owner=request.user, is_player_controlled=False))
            if len(npcs) != len(data["character_ids"]):
                return JsonResponse({"error": "只能选择自己的 NPC"}, status=400)
        actor_ids = data.get("auto_actor_ids", conversation.auto_actor_ids)
        available = {str(npc.id) for npc in npcs} if npcs is not None else set(npc_ids(conversation))
        if len(actor_ids) != len(set(actor_ids)) or any(actor_id not in available for actor_id in actor_ids):
            return JsonResponse({"error": "自动回复角色无效"}, status=400)
        with transaction.atomic():
            if npcs is not None:
                ConversationParticipant.objects.filter(conversation=conversation).exclude(character=conversation.player_character).delete()
                for position, npc in enumerate(npcs, start=1):
                    ConversationParticipant.objects.create(conversation=conversation, character=npc, position=position)
            for field in ("title", "environment", "language", "auto_generate", "auto_actor_ids", "auto_mode"):
                if field in data:
                    setattr(conversation, field, data[field].strip() if field == "title" else data[field])
            conversation.save()
            if any(key in data for key in ('environment','language','character_ids')):
                Conversation.objects.filter(pk=conversation.pk).update(story_revision=F('story_revision')+1)
                conversation.refresh_from_db()
    return JsonResponse(conversation_payload(conversation, include_messages=True))


@require_POST
def send_message(request, conversation_id):
    error = authentication_error(request)
    if error:
        return error
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    data, error = body_or_error(request)
    if error:
        return error
    content = data.get("content", "")
    kind = data.get("kind", Message.DIALOGUE)
    speaker = conversation.participants.filter(pk=data.get("speaker_id")).first()
    if not isinstance(content, str) or not content.strip() or len(content) > 10000 or kind not in {
        Message.DIALOGUE, Message.ACTION, Message.OOC
    } or not speaker or (kind == Message.OOC and not speaker.is_player_controlled):
        return JsonResponse({"error": "消息内容无效"}, status=400)

    with transaction.atomic():
        saved_content = content.strip()
        if kind == Message.OOC and not saved_content.startswith("//"):
            saved_content = f"// {saved_content}"
        message = Message.objects.create(conversation=conversation, speaker=speaker, kind=kind, content=saved_content)
        conversation.save(update_fields=["updated_at"])
        job = None
        warning = None
        if conversation.auto_generate:
            actor_ids = conversation.auto_actor_ids or npc_ids(conversation)
            if actor_ids:
                try:
                    job = queue_generation(conversation, request.user, actor_ids, mode="serial", hints={}, auto=True)
                except ValueError as exc:
                    warning = str(exc)
    result = message_payload(message)
    result["job_id"] = str(job.id) if job else None
    result["warning"] = warning
    return JsonResponse(result, status=201)
