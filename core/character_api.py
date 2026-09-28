import json
import uuid
import base64
from io import BytesIO

from django.core.files.base import ContentFile
from django.db.models import ProtectedError, Q
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth_api import body_or_error
from .models import Character, CharacterCategory


def character_payload(character):
    return {
        "id": str(character.id),
        "name": character.name,
        "summary": character.summary,
        "personality": character.personality,
        "speech_habits": character.speech_habits,
        "memories": character.memories,
        "relationship_notes": character.relationship_notes,
        "state_fields": character.state_fields,
        "affinity": character.affinity,
        "clothing_type": character.clothing_type,
        "clothing_state": character.clothing_state,
        "category_ids": [str(item) for item in character.categories.values_list("id", flat=True)],
        "is_player_controlled": character.is_player_controlled,
        "avatar_url": f"/api/avatars/{character.id}/" if character.avatar else None,
    }


def authentication_error(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=401)
    return None


@require_http_methods(["GET", "POST"])
def characters(request):
    error = authentication_error(request)
    if error:
        return error
    if request.method == "GET":
        query = request.GET.get("q", "").strip()[:100]
        rows = Character.objects.filter(owner=request.user)
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(summary__icontains=query))
        return JsonResponse(
            {"characters": [character_payload(item) for item in rows]}
        )

    data, error = body_or_error(request)
    if error:
        return error
    character = Character(owner=request.user)
    error = set_character_fields(character, data)
    if error:
        return error
    character.save()
    try:
        set_character_categories(character, data)
    except ValueError:
        character.delete()
        return JsonResponse({"error": "角色分类无效"}, status=400)
    return JsonResponse(character_payload(character), status=201)


def set_character_fields(character, data):
    allowed = {"name", "summary", "personality", "speech_habits", "memories", "relationship_notes", "state_fields", "is_player_controlled", "affinity", "clothing_type", "clothing_state", "category_ids"}
    if not data or any(key not in allowed for key in data):
        return JsonResponse({"error": "角色卡内容无效"}, status=400)
    for field, maximum in (("name", 60), ("summary", 200), ("personality", 10000), ("speech_habits", 5000), ("memories", 10000)):
        if field in data:
            value = data[field]
            if not isinstance(value, str) or len(value) > maximum or (field == "name" and not value.strip()):
                return JsonResponse({"error": "角色卡内容无效"}, status=400)
            setattr(character, field, value.strip() if field == "name" else value)
    for field in ("state_fields", "relationship_notes"):
        if field in data:
            value = data[field]
            if not isinstance(value, dict) or len(value) > 50:
                return JsonResponse({"error": "角色卡内容无效"}, status=400)
            if any(not isinstance(key, str) or not 1 <= len(key) <= 50 or len(json.dumps(item, ensure_ascii=False)) > 2000 for key, item in value.items()):
                return JsonResponse({"error": "角色卡内容无效"}, status=400)
            setattr(character, field, value)
    if "is_player_controlled" in data:
        if not isinstance(data["is_player_controlled"], bool):
            return JsonResponse({"error": "角色卡内容无效"}, status=400)
        if character.pk and character.is_player_controlled != data["is_player_controlled"] and character.conversations.exists():
            return JsonResponse({"error": "角色已用于对话，不能改变控制方式"}, status=409)
        character.is_player_controlled = data["is_player_controlled"]
    if "affinity" in data:
        if not isinstance(data["affinity"], int) or isinstance(data["affinity"], bool) or not 0 <= data["affinity"] <= 100:
            return JsonResponse({"error": "好感度必须为 0 到 100"}, status=400)
        character.affinity = data["affinity"]
    for field in ("clothing_type", "clothing_state"):
        if field in data:
            if not isinstance(data[field], str) or len(data[field]) > 200:
                return JsonResponse({"error": "衣服状态内容无效"}, status=400)
            setattr(character, field, data[field].strip())
    if "category_ids" in data and (not isinstance(data["category_ids"], list) or len(data["category_ids"]) > 100):
        return JsonResponse({"error": "角色分类无效"}, status=400)
    return None


def set_character_categories(character, data):
    if "category_ids" not in data:
        return
    ids = list(dict.fromkeys(str(item) for item in data["category_ids"]))
    categories = CharacterCategory.objects.filter(owner=character.owner, id__in=ids)
    if categories.count() != len(ids):
        raise ValueError("角色分类无效")
    character.categories.set(categories)


@require_http_methods(["GET", "PATCH", "DELETE"])
def character_detail(request, character_id):
    error = authentication_error(request)
    if error:
        return error
    character = get_object_or_404(Character, pk=character_id, owner=request.user)
    if request.method == "PATCH":
        data, error = body_or_error(request)
        if error:
            return error
        error = set_character_fields(character, data)
        if error:
            return error
        character.save()
        try:
            set_character_categories(character, data)
        except ValueError:
            return JsonResponse({"error": "角色分类无效"}, status=400)
    elif request.method == "DELETE":
        try:
            character.delete()
        except ProtectedError:
            return JsonResponse({"error": "角色仍在对话中，请先删除相关对话"}, status=409)
        if character.avatar:
            character.avatar.delete(save=False)
        return JsonResponse({}, status=204)
    return JsonResponse(character_payload(character))


@require_POST
def character_avatar(request, character_id):
    error = authentication_error(request)
    if error:
        return error
    character = get_object_or_404(Character, pk=character_id, owner=request.user)
    uploaded = request.FILES.get("avatar")
    if not uploaded or uploaded.size > 2 * 1024 * 1024:
        return JsonResponse({"error": "头像需小于 2 MB"}, status=400)
    try:
        image = Image.open(uploaded)
        image.verify()
        uploaded.seek(0)
        image = ImageOps.exif_transpose(Image.open(uploaded))
        if image.width > 4096 or image.height > 4096:
            return JsonResponse({"error": "头像尺寸过大"}, status=400)
        image.thumbnail((512, 512))
        image = image.convert("RGBA")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return JsonResponse({"error": "头像图片无效"}, status=400)
    old_avatar = character.avatar.name
    character.avatar.save(f"{uuid.uuid4().hex}.png", ContentFile(output.getvalue()), save=True)
    if old_avatar:
        character.avatar.storage.delete(old_avatar)
    return JsonResponse(character_payload(character))


@require_GET
def avatar_file(request, character_id):
    error = authentication_error(request)
    if error:
        return error
    character = get_object_or_404(Character, pk=character_id)
    if character.owner_id != request.user.id and not request.user.is_staff:
        return JsonResponse({"error": "未找到"}, status=404)
    if not character.avatar:
        return JsonResponse({"error": "未找到"}, status=404)
    return FileResponse(character.avatar.open("rb"), content_type="image/png")


def category_payload(category, children=None):
    return {
        "id": str(category.id), "name": category.name,
        "parent_id": str(category.parent_id) if category.parent_id else None,
        "position": category.position,
        "children": children or [],
    }


def category_tree(user):
    rows = list(CharacterCategory.objects.filter(owner=user))
    children = {}
    for row in rows:
        children.setdefault(row.parent_id, []).append(row)
    def build(parent_id):
        return [category_payload(row, build(row.id)) for row in children.get(parent_id, [])]
    return build(None)


@require_http_methods(["GET", "POST"])
def character_categories(request):
    error = authentication_error(request)
    if error:
        return error
    if request.method == "GET":
        return JsonResponse({"categories": category_tree(request.user)})
    data, error = body_or_error(request)
    if error:
        return error
    name = data.get("name", "") if isinstance(data, dict) else ""
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        return JsonResponse({"error": "分类名称无效"}, status=400)
    parent = None
    if data.get("parent_id"):
        parent = get_object_or_404(CharacterCategory, pk=data["parent_id"], owner=request.user)
    category = CharacterCategory.objects.create(owner=request.user, parent=parent, name=name.strip(), position=data.get("position", 0))
    return JsonResponse(category_payload(category), status=201)


@require_http_methods(["PATCH", "DELETE"])
def character_category_detail(request, category_id):
    error = authentication_error(request)
    if error:
        return error
    category = get_object_or_404(CharacterCategory, pk=category_id, owner=request.user)
    if request.method == "DELETE":
        category.delete()
        return JsonResponse({}, status=204)
    data, error = body_or_error(request)
    if error:
        return error
    if "name" in data:
        if not isinstance(data["name"], str) or not data["name"].strip() or len(data["name"]) > 120:
            return JsonResponse({"error": "分类名称无效"}, status=400)
        category.name = data["name"].strip()
    if "parent_id" in data:
        category.parent = get_object_or_404(CharacterCategory, pk=data["parent_id"], owner=request.user) if data["parent_id"] else None
    try:
        category.save()
    except Exception:
        return JsonResponse({"error": "分类层级无效"}, status=400)
    return JsonResponse(category_payload(category))


_CHARACTER_NAME_KEYS = (
    "name", "char_name", "character_name", "charName", "characterName",
    "角色名", "角色名称", "姓名", "名字", "title",
)
_CHARACTER_DATA_KEYS = ("data", "character", "character_data", "card", "profile")


def _decode_character_mapping(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("角色卡 JSON 无效") from exc
    return value if isinstance(value, dict) else None


def _character_mapping(item):
    data = _decode_character_mapping(item)
    for _ in range(6):
        if data is None:
            break
        name = next((data.get(key) for key in _CHARACTER_NAME_KEYS if isinstance(data.get(key), str) and data.get(key).strip()), None)
        if name:
            return data
        nested = next((data.get(key) for key in _CHARACTER_DATA_KEYS if key in data), None)
        next_data = _decode_character_mapping(nested)
        if next_data is None or next_data is data:
            break
        data = next_data
    return data


def _native_character(item, *, default_categories=None):
    data = _character_mapping(item)
    if not isinstance(data, dict):
        raise ValueError("角色卡内容无效")
    name = next((data.get(key) for key in _CHARACTER_NAME_KEYS if isinstance(data.get(key), str) and data.get(key).strip()), "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("角色卡缺少名称")
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        categories = default_categories or []
    return {
        "name": name.strip()[:60],
        "summary": str(data.get("summary", data.get("description", "")))[:200],
        "personality": str(data.get("personality", ""))[:10000],
        "speech_habits": str(data.get("speech_habits", data.get("mes_example", "")))[:5000],
        "memories": str(data.get("memories", data.get("scenario", "")))[:10000],
        "relationship_notes": data.get("relationship_notes", {}) if isinstance(data.get("relationship_notes", {}), dict) else {},
        "state_fields": data.get("state_fields", {}) if isinstance(data.get("state_fields", {}), dict) else {},
        "affinity": max(0, min(100, int(data.get("affinity", 0)))) if str(data.get("affinity", 0)).lstrip("-").isdigit() else 0,
        "clothing_type": str(data.get("clothing_type", ""))[:200],
        "clothing_state": str(data.get("clothing_state", ""))[:200],
        "is_player_controlled": bool(data.get("is_player_controlled", False)),
        "categories": [str(value)[:500] for value in categories if isinstance(value, str)][:100],
    }


def _parse_character_payload(payload):
    try:
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 5 * 1024 * 1024:
            raise ValueError("角色卡文件需小于 5 MB")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("角色卡 JSON 无效") from exc
    if not isinstance(payload, (dict, list)):
        raise ValueError("角色卡 JSON 无效")
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("角色卡 JSON 无效") from exc
    container = payload if isinstance(payload, dict) else {}
    source = "native" if container.get("format") == "neon-tavern-character" else "tavern"
    top_level_category = container.get("category")
    if isinstance(top_level_category, str):
        default_categories = [top_level_category]
    elif isinstance(top_level_category, list):
        default_categories = [value for value in top_level_category if isinstance(value, str)]
    else:
        default_categories = []
    items = container.get("characters")
    if items is None:
        items = container.get("cards")
    if items is None and isinstance(container.get("data"), list):
        items = container["data"]
    if items is None:
        items = payload if isinstance(payload, list) else [payload]
    if not isinstance(items, list):
        items = [items]
    characters = [_native_character(item, default_categories=default_categories) for item in items[:100]]
    return {"source_format": source, "characters": characters, "warnings": []}


def _parse_character_upload(uploaded):
    if not uploaded or uploaded.size > 5 * 1024 * 1024:
        raise ValueError("角色卡文件需小于 5 MB")
    raw = uploaded.read()
    if raw.startswith(b"\x89PNG"):
        image = Image.open(BytesIO(raw))
        encoded = image.info.get("chara") or image.info.get("ccv3")
        if not encoded:
            raise ValueError("PNG 中没有可识别的角色卡信息")
        try:
            raw = base64.b64decode(encoded)
        except Exception as exc:
            raise ValueError("PNG 角色卡信息无效") from exc
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("角色卡 JSON 无效") from exc
    return _parse_character_payload(payload)


@require_POST
def character_import_preview(request):
    error = authentication_error(request)
    if error:
        return error
    try:
        if request.FILES.get("file"):
            preview = _parse_character_upload(request.FILES.get("file"))
        else:
            data, error = body_or_error(request)
            if error:
                return error
            preview = _parse_character_payload(data.get("payload")) if isinstance(data, dict) else None
            if preview is None:
                raise ValueError("角色卡文件无效")
    except (ValueError, OSError, UnidentifiedImageError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    existing = set(Character.objects.filter(owner=request.user, name__in=[item["name"] for item in preview["characters"]]).values_list("name", flat=True))
    for item in preview["characters"]:
        item["conflict"] = item["name"] in existing
    return JsonResponse(preview)


def _ensure_category_path(user, path):
    parent = None
    for name in [part.strip() for part in path.split("/") if part.strip()]:
        category, _ = CharacterCategory.objects.get_or_create(owner=user, parent=parent, name=name[:120])
        parent = category
    return parent


@require_POST
def character_import_commit(request):
    error = authentication_error(request)
    if error:
        return error
    data, error = body_or_error(request)
    if error:
        return error
    imported = []
    for raw in data.get("characters", [])[:100]:
        item = _native_character(raw)
        name = item.pop("name")
        paths = item.pop("categories")
        item.pop("conflict", None)
        if Character.objects.filter(owner=request.user, name=name).exists():
            name = f"{name}（导入）"[:60]
        character = Character.objects.create(owner=request.user, name=name, **item)
        for category_path in paths:
            category = _ensure_category_path(request.user, category_path)
            if category:
                category.characters.add(character)
        imported.append(character_payload(character))
    return JsonResponse({"characters": imported}, status=201)


@require_GET
def export_character(request, character_id):
    error = authentication_error(request)
    if error:
        return error
    character = get_object_or_404(Character, pk=character_id, owner=request.user)
    item = character_payload(character)
    item.pop("id", None); item.pop("avatar_url", None); item.pop("category_ids", None)
    item["categories"] = [category.name for category in character.categories.all()]
    response = JsonResponse({"format": "neon-tavern-character", "version": 1, "characters": [item]}, json_dumps_params={"ensure_ascii": False})
    response["Content-Disposition"] = f'attachment; filename="character-{character.id}.json"'
    return response


@require_GET
def export_character_category(request, category_id):
    error = authentication_error(request)
    if error:
        return error
    root = get_object_or_404(CharacterCategory, pk=category_id, owner=request.user)
    categories = []
    def visit(node):
        categories.append(node)
        for child in node.children.all().order_by("position", "name", "id"):
            visit(child)
    visit(root)
    included_ids = {item.id for item in categories}
    characters = list(Character.objects.filter(owner=request.user, categories__id__in=included_ids).distinct().prefetch_related("categories"))
    order = {item.id: index for index, item in enumerate(categories)}
    def category_path(item):
        parts = [item.name]
        node = item
        while node.parent_id and node.parent_id in included_ids:
            node = next(parent for parent in categories if parent.id == node.parent_id)
            parts.append(node.name)
        return "/".join(reversed(parts))
    rows = []
    for character in characters:
        item = character_payload(character)
        item.pop("id", None); item.pop("avatar_url", None); item.pop("category_ids", None)
        item["categories"] = [category_path(category) for category in sorted(character.categories.all(), key=lambda value: order.get(value.id, 999999)) if category.id in included_ids]
        rows.append(item)
    payload = {"format": "neon-tavern-character", "version": 1, "category": root.name, "characters": rows}
    response = JsonResponse(payload, json_dumps_params={"ensure_ascii": False})
    response["Content-Disposition"] = f'attachment; filename="characters-{root.id}.json"'
    return response
