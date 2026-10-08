import copy
import json
from dataclasses import dataclass, field

from django.db import transaction

from .character_api import _native_character
from .character_lore import EXTENDED_FIELDS
from .models import Character, CharacterCategory, Worldbook
from .worldbook_transfer import ImportValidationError, commit_import, export_native, parse_import


FORMAT = "neon-tavern-bundle"
VERSION = 2
MAX_BYTES = 20 * 1024 * 1024
MAX_CHARACTERS = 100
MAX_WORLDBOOKS = 100


@dataclass
class BundlePreview:
    characters: list[dict]
    worldbooks: list[dict]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "format": FORMAT,
            "version": VERSION,
            "characters": self.characters,
            "worldbooks": [
                {"package_id": row["package_id"], "conflict": row["conflict"], **row["preview"].as_dict()}
                for row in self.worldbooks
            ],
            "counts": {
                "characters": len(self.characters),
                "worldbooks": len(self.worldbooks),
                "worldbook_categories": sum(len(row["preview"].categories) for row in self.worldbooks),
                "worldbook_entries": sum(len(row["preview"].entries) for row in self.worldbooks),
            },
            "warnings": self.warnings,
        }


def _serialized_size(payload):
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ImportValidationError("整合包不是有效的 JSON 数据") from exc


def _category_path(category):
    parts = [category.name]
    node = category
    seen = {str(node.pk)}
    while node.parent_id:
        node = node.parent
        if str(node.pk) in seen:
            raise ImportValidationError("角色分类层级存在循环")
        seen.add(str(node.pk))
        parts.append(node.name)
        if len(parts) > 50:
            raise ImportValidationError("角色分类层级超过 50 层")
    return "/".join(reversed(parts))


def export_bundle(owner, character_ids, worldbook_ids):
    if not isinstance(character_ids, list) or not isinstance(worldbook_ids, list):
        raise ImportValidationError("请选择要导出的角色卡和世界书")
    character_ids = list(dict.fromkeys(str(item) for item in character_ids))
    worldbook_ids = list(dict.fromkeys(str(item) for item in worldbook_ids))
    if not character_ids and not worldbook_ids:
        raise ImportValidationError("至少选择一张角色卡或一本世界书")
    if len(character_ids) > MAX_CHARACTERS or len(worldbook_ids) > MAX_WORLDBOOKS:
        raise ImportValidationError("整合包最多包含 100 张角色卡和 100 本世界书")

    characters = list(Character.objects.filter(owner=owner, id__in=character_ids).prefetch_related("categories"))
    books = list(Worldbook.objects.filter(owner=owner, id__in=worldbook_ids).prefetch_related("entries"))
    if len(characters) != len(character_ids) or len(books) != len(worldbook_ids):
        raise ImportValidationError("所选素材不存在或不属于当前账号")

    character_rows = []
    package_character_ids = {str(item.id) for item in characters}
    for character in characters:
        row = {
            **{key:getattr(character,key) for key in EXTENDED_FIELDS},
            "package_id": str(character.id),
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
            "is_player_controlled": character.is_player_controlled,
            "categories": [_category_path(item) for item in character.categories.all()],
        }
        character_rows.append(row)

    worldbook_rows = []
    for book in books:
        payload = export_native(book)
        omitted_conversation_scopes = 0
        for entry in payload["entries"]:
            source_ids = entry.get("scoped_character_ids", [])
            entry["scoped_character_ids"] = [value for value in source_ids if value in package_character_ids]
            entry["omitted_external_character_scopes"] = len(source_ids) - len(entry["scoped_character_ids"])
            omitted_conversation_scopes += len(entry.get("scoped_conversation_ids", []))
            entry["scoped_conversation_ids"] = []
        worldbook_rows.append({
            "package_id": str(book.id),
            "payload": payload,
            "omitted_conversation_scopes": omitted_conversation_scopes,
        })

    result = {"format": FORMAT, "version": VERSION, "characters": character_rows, "worldbooks": worldbook_rows}
    if _serialized_size(result) > MAX_BYTES:
        raise ImportValidationError("整合包不能超过 20 MB")
    return result


def parse_bundle(payload, owner):
    if not isinstance(payload, dict) or _serialized_size(payload) > MAX_BYTES:
        raise ImportValidationError("整合包内容无效或超过 20 MB")
    if payload.get("format") != FORMAT or payload.get("version") not in (1,VERSION):
        raise ImportValidationError("不支持的整合包格式或版本")
    raw_characters = payload.get("characters")
    raw_books = payload.get("worldbooks")
    if not isinstance(raw_characters, list) or not isinstance(raw_books, list):
        raise ImportValidationError("整合包缺少角色卡或世界书列表")
    if len(raw_characters) > MAX_CHARACTERS or len(raw_books) > MAX_WORLDBOOKS:
        raise ImportValidationError("整合包最多包含 100 张角色卡和 100 本世界书")
    if not raw_characters and not raw_books:
        raise ImportValidationError("整合包中没有角色卡或世界书")

    package_ids = set()
    characters = []
    for row in raw_characters:
        if not isinstance(row, dict):
            raise ImportValidationError("角色卡数据无效")
        package_id = row.get("package_id")
        if not isinstance(package_id, str) or not package_id or len(package_id) > 128 or package_id in package_ids:
            raise ImportValidationError("角色卡引用无效或重复")
        package_ids.add(package_id)
        normalized = _native_character(row)
        normalized["package_id"] = package_id
        characters.append(normalized)

    warnings = []
    known_character_ids = {item["package_id"] for item in characters}
    worldbooks = []
    worldbook_package_ids = set()
    for row in raw_books:
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            raise ImportValidationError("世界书数据无效")
        package_id = row.get("package_id")
        if not isinstance(package_id, str) or not package_id or len(package_id) > 128 or package_id in worldbook_package_ids:
            raise ImportValidationError("世界书引用无效或重复")
        worldbook_package_ids.add(package_id)
        native_payload = copy.deepcopy(row["payload"])
        parsed = parse_import(native_payload, "native")
        for entry in parsed.entries:
            source_ids = entry["scoped_character_ids"]
            valid_ids = [value for value in source_ids if value in known_character_ids]
            if len(valid_ids) != len(source_ids):
                warnings.append(f"世界书“{parsed.worldbook['name']}”有角色范围不在整合包内，导入时会忽略。")
            entry["scoped_character_ids"] = valid_ids
            if entry["scoped_conversation_ids"]:
                warnings.append(f"世界书“{parsed.worldbook['name']}”的对话专属范围不会跨账号导入。")
                entry["scoped_conversation_ids"] = []
        omitted = row.get("omitted_conversation_scopes", 0)
        if isinstance(omitted, int) and omitted > 0:
            warnings.append(f"世界书“{parsed.worldbook['name']}”导出时省略了 {omitted} 个对话专属范围。")
        worldbooks.append({"package_id": package_id, "preview": parsed})

    existing_character_names = set(Character.objects.filter(owner=owner, name__in=[item["name"] for item in characters]).values_list("name", flat=True))
    for item in characters:
        item["conflict"] = item["name"] in existing_character_names
    existing_book_names = set(Worldbook.objects.filter(owner=owner, name__in=[item["preview"].worldbook["name"] for item in worldbooks]).values_list("name", flat=True))
    for item in worldbooks:
        item["conflict"] = item["preview"].worldbook["name"] in existing_book_names
    if any(row.get("avatar") or row.get("avatar_url") for row in raw_characters):
        warnings.append("整合包不包含角色头像图片；导入后需单独设置头像。")
    return BundlePreview(characters, worldbooks, list(dict.fromkeys(warnings)))


def _available_character_name(owner, requested):
    if not Character.objects.filter(owner=owner, name=requested).exists():
        return requested
    suffix = 2
    while True:
        candidate = f"{requested}（导入 {suffix}）"[:60]
        if not Character.objects.filter(owner=owner, name=candidate).exists():
            return candidate
        suffix += 1


def _ensure_category_path(owner, path):
    parent = None
    for part in [value.strip() for value in path.split("/") if value.strip()]:
        if len(part) > 120:
            raise ImportValidationError("角色分类名称不能超过 120 个字符")
        category, _ = CharacterCategory.objects.get_or_create(owner=owner, parent=parent, name=part)
        parent = category
    return parent


@transaction.atomic
def commit_bundle(preview, owner):
    if not isinstance(preview, BundlePreview):
        raise ImportValidationError("整合包预览无效")
    character_id_map = {}
    imported_characters = []
    for raw in preview.characters:
        item = _native_character(raw)
        package_id = raw["package_id"]
        paths = item.pop("categories")
        item.pop("name")
        character = Character.objects.create(owner=owner, name=_available_character_name(owner, raw["name"]), **item)
        for category_path in paths:
            category = _ensure_category_path(owner, category_path)
            if category:
                category.characters.add(character)
        character_id_map[package_id] = str(character.id)
        imported_characters.append({"id": str(character.id), "name": character.name})

    imported_worldbooks = []
    for row in preview.worldbooks:
        parsed = copy.deepcopy(row["preview"])
        for entry in parsed.entries:
            entry["scoped_character_ids"] = [
                character_id_map[value]
                for value in entry["scoped_character_ids"]
                if value in character_id_map
            ]
        book = commit_import(parsed, owner, conflict_policy="keep_both")
        imported_worldbooks.append({"id": str(book.id), "name": book.name})
    return {
        "characters": imported_characters,
        "worldbooks": imported_worldbooks,
        "warnings": preview.warnings,
    }
