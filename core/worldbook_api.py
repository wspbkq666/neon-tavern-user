import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .auth_api import body_or_error
from .character_api import authentication_error
from .models import Character, Conversation, ConversationWorldbookConfig, Worldbook, WorldbookCategory, WorldbookEntry
from .worldbook_resolver import preview_worldbook_entries
from .worldbook_transfer import ImportValidationError, commit_import, export_native, parse_import


def worldbook_payload(item, *, expanded=False):
    result = {
        "id": str(item.id),
        "name": item.name,
        "description": item.description,
        "enabled": item.enabled,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if expanded:
        result["categories"] = [category_payload(row) for row in item.categories.all()]
        result["entries"] = [entry_payload(row) for row in item.entries.prefetch_related("categories", "scoped_characters", "scoped_conversations")]
    return result


def category_payload(item):
    return {
        "id": str(item.id),
        "worldbook_id": str(item.worldbook_id),
        "parent_id": str(item.parent_id) if item.parent_id else None,
        "name": item.name,
        "position": item.position,
    }


def entry_payload(item):
    return {
        "id": str(item.id),
        "worldbook_id": str(item.worldbook_id),
        "name": item.name,
        "content": item.content,
        "enabled": item.enabled,
        "trigger_mode": item.trigger_mode,
        "keywords": item.keywords,
        "insertion_position": item.insertion_position,
        "priority": item.priority,
        "scope_type": item.scope_type,
        "category_ids": [str(value) for value in item.categories.values_list("id", flat=True)],
        "scoped_character_ids": [str(value) for value in item.scoped_characters.values_list("id", flat=True)],
        "scoped_conversation_ids": [str(value) for value in item.scoped_conversations.values_list("id", flat=True)],
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _uuid_list(value, *, limit=5000):
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(item, str) for item in value):
        return None
    try:
        parsed = [uuid.UUID(item) for item in value]
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if len(parsed) == len(set(parsed)) else None


def _text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        return None
    return value.strip() if required else value


@require_http_methods(["GET", "POST"])
def worldbooks(request):
    if error := authentication_error(request):
        return error
    if request.method == "GET":
        return JsonResponse({"worldbooks": [worldbook_payload(item) for item in Worldbook.objects.filter(owner=request.user)]})
    data, error = body_or_error(request)
    if error:
        return error
    if not isinstance(data, dict) or set(data) - {"name", "description", "enabled"}:
        return JsonResponse({"error": "世界书内容无效"}, status=400)
    name = _text(data.get("name"), 120, required=True)
    description = _text(data.get("description", ""), 10000)
    enabled = data.get("enabled", True)
    if name is None or description is None or not isinstance(enabled, bool):
        return JsonResponse({"error": "世界书内容无效"}, status=400)
    try:
        item = Worldbook.objects.create(owner=request.user, name=name, description=description, enabled=enabled)
    except IntegrityError:
        return JsonResponse({"error": "世界书名称已存在"}, status=409)
    return JsonResponse(worldbook_payload(item), status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
def worldbook_detail(request, worldbook_id):
    if error := authentication_error(request):
        return error
    item = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    if request.method == "DELETE":
        item.delete()
        return JsonResponse({}, status=204)
    if request.method == "PATCH":
        data, error = body_or_error(request)
        if error:
            return error
        if not isinstance(data, dict) or not data or set(data) - {"name", "description", "enabled"}:
            return JsonResponse({"error": "世界书内容无效"}, status=400)
        if "name" in data:
            value = _text(data["name"], 120, required=True)
            if value is None:
                return JsonResponse({"error": "世界书内容无效"}, status=400)
            item.name = value
        if "description" in data:
            value = _text(data["description"], 10000)
            if value is None:
                return JsonResponse({"error": "世界书内容无效"}, status=400)
            item.description = value
        if "enabled" in data:
            if not isinstance(data["enabled"], bool):
                return JsonResponse({"error": "世界书内容无效"}, status=400)
            item.enabled = data["enabled"]
        try:
            item.save()
        except IntegrityError:
            return JsonResponse({"error": "世界书名称已存在"}, status=409)
    return JsonResponse(worldbook_payload(item, expanded=True))


def _category_fields(item, data):
    if not isinstance(data, dict) or set(data) - {"name", "parent_id", "position"}:
        raise ValueError
    if "name" in data:
        value = _text(data["name"], 120, required=True)
        if value is None:
            raise ValueError
        item.name = value
    if "position" in data:
        if not isinstance(data["position"], int) or not 0 <= data["position"] <= 1_000_000:
            raise ValueError
        item.position = data["position"]
    if "parent_id" in data:
        if data["parent_id"] is None:
            item.parent = None
        else:
            try:
                parent_id = uuid.UUID(data["parent_id"])
            except (ValueError, TypeError, AttributeError):
                raise ValueError
            item.parent = get_object_or_404(WorldbookCategory, pk=parent_id, worldbook=item.worldbook)


@require_http_methods(["POST"])
def categories(request, worldbook_id):
    if error := authentication_error(request):
        return error
    book = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    data, error = body_or_error(request)
    if error:
        return error
    item = WorldbookCategory(worldbook=book)
    try:
        _category_fields(item, data)
        if not item.name:
            raise ValueError
        item.save()
    except ValueError:
        return JsonResponse({"error": "分类内容无效"}, status=400)
    except ValidationError as exc:
        return JsonResponse({"error": "分类层级或名称无效", "detail": exc.message_dict}, status=409)
    return JsonResponse(category_payload(item), status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
def category_detail(request, worldbook_id, category_id):
    if error := authentication_error(request):
        return error
    book = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    item = get_object_or_404(WorldbookCategory, pk=category_id, worldbook=book)
    if request.method == "DELETE":
        with transaction.atomic():
            item.delete()
        return JsonResponse({}, status=204)
    if request.method == "PATCH":
        data, error = body_or_error(request)
        if error:
            return error
        try:
            with transaction.atomic():
                _category_fields(item, data)
                item.save()
        except ValueError:
            return JsonResponse({"error": "分类内容无效"}, status=400)
        except ValidationError:
            return JsonResponse({"error": "分类层级或名称无效"}, status=409)
    return JsonResponse(category_payload(item))


def _set_entry_fields(item, data, *, creation=False):
    allowed = {"name", "content", "enabled", "trigger_mode", "keywords", "insertion_position", "priority", "scope_type", "category_ids", "scoped_character_ids", "scoped_conversation_ids"}
    if not isinstance(data, dict) or not data or set(data) - allowed:
        raise ValueError
    if creation and not {"name", "content"}.issubset(data):
        raise ValueError
    for field, maximum, required in (("name", 160, True), ("content", 100000, False)):
        if field in data:
            value = _text(data[field], maximum, required=required)
            if value is None:
                raise ValueError
            setattr(item, field, value)
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ValueError
        item.enabled = data["enabled"]
    for field, choices in (("trigger_mode", dict(WorldbookEntry.TRIGGER_CHOICES)), ("insertion_position", dict(WorldbookEntry.POSITION_CHOICES)), ("scope_type", dict(WorldbookEntry.SCOPE_CHOICES))):
        if field in data:
            if data[field] not in choices:
                raise ValueError
            setattr(item, field, data[field])
    if "priority" in data:
        if not isinstance(data["priority"], int) or not -1_000_000 <= data["priority"] <= 1_000_000:
            raise ValueError
        item.priority = data["priority"]
    if "keywords" in data:
        if not isinstance(data["keywords"], list) or len(data["keywords"]) > 100 or not all(isinstance(value, str) and len(value) <= 200 for value in data["keywords"]):
            raise ValueError
        item.keywords = list(dict.fromkeys(value.strip().casefold() for value in data["keywords"] if value.strip()))


def _replace_entry_relations(item, data, user):
    relations = (
        ("category_ids", item.categories, WorldbookCategory.objects.filter(worldbook=item.worldbook)),
        ("scoped_character_ids", item.scoped_characters, Character.objects.filter(owner=user)),
        ("scoped_conversation_ids", item.scoped_conversations, Conversation.objects.filter(owner=user)),
    )
    for field, manager, queryset in relations:
        if field not in data:
            continue
        ids = _uuid_list(data[field])
        if ids is None:
            raise ValueError
        objects = list(queryset.filter(id__in=ids))
        if len(objects) != len(ids):
            raise ValueError
        manager.set(objects)


@require_http_methods(["GET", "POST"])
def entries(request, worldbook_id):
    if error := authentication_error(request):
        return error
    book = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    if request.method == "GET":
        rows = book.entries.prefetch_related("categories", "scoped_characters", "scoped_conversations")
        return JsonResponse({"entries": [entry_payload(item) for item in rows]})
    data, error = body_or_error(request)
    if error:
        return error
    item = WorldbookEntry(worldbook=book)
    try:
        with transaction.atomic():
            _set_entry_fields(item, data, creation=True)
            item.full_clean()
            item.save()
            _replace_entry_relations(item, data, request.user)
    except (ValueError, ValidationError):
        return JsonResponse({"error": "条目内容或关联无效"}, status=400)
    except IntegrityError:
        return JsonResponse({"error": "条目名称已存在"}, status=409)
    return JsonResponse(entry_payload(item), status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
def entry_detail(request, worldbook_id, entry_id):
    if error := authentication_error(request):
        return error
    book = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    item = get_object_or_404(WorldbookEntry, pk=entry_id, worldbook=book)
    if request.method == "DELETE":
        item.delete()
        return JsonResponse({}, status=204)
    if request.method == "PATCH":
        data, error = body_or_error(request)
        if error:
            return error
        try:
            with transaction.atomic():
                _set_entry_fields(item, data)
                item.full_clean()
                item.save()
                _replace_entry_relations(item, data, request.user)
        except (ValueError, ValidationError):
            return JsonResponse({"error": "条目内容或关联无效"}, status=400)
        except IntegrityError:
            return JsonResponse({"error": "条目名称已存在"}, status=409)
    return JsonResponse(entry_payload(item))


@require_http_methods(["POST"])
def bulk_entry_categories(request, worldbook_id):
    if error := authentication_error(request):
        return error
    book = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    data, error = body_or_error(request)
    if error:
        return error
    entry_ids = _uuid_list(data.get("entry_ids")) if isinstance(data, dict) else None
    category_ids = _uuid_list(data.get("category_ids")) if isinstance(data, dict) else None
    action = data.get("action") if isinstance(data, dict) else None
    if entry_ids is None or category_ids is None or not entry_ids or action not in {"add", "remove"}:
        return JsonResponse({"error": "批量分类内容无效"}, status=400)
    rows = list(book.entries.filter(id__in=entry_ids))
    categories_list = list(book.categories.filter(id__in=category_ids))
    if len(rows) != len(entry_ids) or len(categories_list) != len(category_ids):
        return JsonResponse({"error": "批量分类内容无效"}, status=400)
    with transaction.atomic():
        for entry in rows:
            getattr(entry.categories, action)(*categories_list)
    return JsonResponse({"entries": [entry_payload(item) for item in rows]})


def selection_payload(config):
    return {
        "enabled_worldbook_ids": [str(value) for value in config.enabled_worldbooks.values_list("id", flat=True)],
        "enabled_category_ids": [str(value) for value in config.enabled_categories.values_list("id", flat=True)],
        "excluded_category_ids": [str(value) for value in config.excluded_categories.values_list("id", flat=True)],
        "excluded_entry_ids": [str(value) for value in config.excluded_entries.values_list("id", flat=True)],
        "manual_entry_ids": [str(value) for value in config.manual_entries.values_list("id", flat=True)],
    }


@require_http_methods(["GET", "PUT"])
def conversation_worldbooks(request, conversation_id):
    if error := authentication_error(request):
        return error
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    config, _ = ConversationWorldbookConfig.objects.get_or_create(conversation=conversation)
    if request.method == "GET":
        return JsonResponse(selection_payload(config))
    data, error = body_or_error(request)
    if error:
        return error
    fields = {
        "enabled_worldbook_ids": (config.enabled_worldbooks, Worldbook.objects.filter(owner=request.user)),
        "enabled_category_ids": (config.enabled_categories, WorldbookCategory.objects.filter(worldbook__owner=request.user)),
        "excluded_category_ids": (config.excluded_categories, WorldbookCategory.objects.filter(worldbook__owner=request.user)),
        "excluded_entry_ids": (config.excluded_entries, WorldbookEntry.objects.filter(worldbook__owner=request.user)),
        "manual_entry_ids": (config.manual_entries, WorldbookEntry.objects.filter(worldbook__owner=request.user)),
    }
    if not isinstance(data, dict) or set(data) != set(fields):
        return JsonResponse({"error": "世界书选择内容无效"}, status=400)
    resolved = {}
    for field, (_, queryset) in fields.items():
        ids = _uuid_list(data[field])
        if ids is None:
            return JsonResponse({"error": "世界书选择内容无效"}, status=400)
        objects = list(queryset.filter(id__in=ids))
        if len(objects) != len(ids):
            return JsonResponse({"error": "世界书选择内容无效"}, status=400)
        resolved[field] = objects
    with transaction.atomic():
        for field, (manager, _) in fields.items():
            manager.set(resolved[field])
    return JsonResponse(selection_payload(config))


@require_http_methods(["POST"])
def preview(request):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if not isinstance(data, dict) or set(data) - {"text", "conversation_id", "actor_id"}:
        return JsonResponse({"error": "预览内容无效"}, status=400)
    text = data.get("text", "")
    if not isinstance(text, str) or len(text) > 20000:
        return JsonResponse({"error": "预览内容无效"}, status=400)
    conversation = None
    actor = None
    if data.get("conversation_id"):
        conversation = get_object_or_404(Conversation, pk=data["conversation_id"], owner=request.user)
    if data.get("actor_id"):
        actor = get_object_or_404(Character, pk=data["actor_id"], owner=request.user)
    rows = preview_worldbook_entries(request.user, text, conversation=conversation, actor=actor)
    return JsonResponse({"entries": [entry_payload(item) for item in rows]})


@require_http_methods(["GET"])
def export_worldbook(request, worldbook_id):
    if error := authentication_error(request):
        return error
    book = get_object_or_404(Worldbook, pk=worldbook_id, owner=request.user)
    return JsonResponse(export_native(book), json_dumps_params={"ensure_ascii": False})


@require_http_methods(["POST"])
def import_preview(request):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if not isinstance(data, dict) or set(data) - {"payload", "source_format"}:
        return JsonResponse({"error": "导入内容无效"}, status=400)
    try:
        parsed = parse_import(data.get("payload"), data.get("source_format", "native"))
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(parsed.as_dict(), json_dumps_params={"ensure_ascii": False})


@require_http_methods(["POST"])
def import_commit(request):
    if error := authentication_error(request):
        return error
    data, error = body_or_error(request)
    if error:
        return error
    if not isinstance(data, dict) or set(data) - {"payload", "source_format", "conflict_policy"}:
        return JsonResponse({"error": "导入内容无效"}, status=400)
    try:
        parsed = parse_import(data.get("payload"), data.get("source_format", "native"))
        book = commit_import(parsed, request.user, conflict_policy=data.get("conflict_policy", "keep_both"))
    except ImportValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"worldbook": worldbook_payload(book, expanded=True), "warnings": parsed.warnings}, status=201)
