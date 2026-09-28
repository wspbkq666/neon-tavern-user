import json
from dataclasses import dataclass, field

from django.db import transaction

from .models import Character, Conversation, Worldbook, WorldbookCategory, WorldbookEntry


FORMAT = "neon-tavern-worldbook"
VERSION = 1
MAX_LOGICAL_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 50
MAX_ENTRIES = 5000
MAX_CATEGORIES = 5000
MAX_CONTENT_LENGTH = 200_000


class ImportValidationError(ValueError):
    pass


@dataclass
class ImportPreview:
    source_format: str
    worldbook: dict
    categories: list[dict]
    entries: list[dict]
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self):
        return {"categories": len(self.categories), "entries": len(self.entries)}

    def as_dict(self):
        return {
            "source_format": self.source_format,
            "worldbook": self.worldbook,
            "categories": self.categories,
            "entries": self.entries,
            "counts": self.counts,
            "warnings": self.warnings,
        }


def _source_id(value):
    return str(value)


def _category_preorder(categories, roots):
    children = {}
    for category in categories:
        children.setdefault(category.parent_id, []).append(category)
    for rows in children.values():
        rows.sort(key=lambda row: (row.position, row.created_at, str(row.id)))
    ordered = []

    def visit(category):
        ordered.append(category)
        for child in children.get(category.id, []):
            visit(child)

    for root in roots:
        visit(root)
    return ordered


def export_native(worldbook, *, category=None, entries=None):
    if category is not None and entries is not None:
        raise ValueError("category and entries exports are mutually exclusive")
    if category is not None and category.worldbook_id != worldbook.id:
        raise ValueError("category does not belong to worldbook")

    all_categories = list(worldbook.categories.all())
    if category is not None:
        ordered_categories = _category_preorder(all_categories, [category])
        included_category_ids = {row.id for row in ordered_categories}
        selected_entries = list(
            worldbook.entries.filter(categories__id__in=included_category_ids)
            .distinct()
            .prefetch_related("categories", "scoped_characters", "scoped_conversations")
        )
    elif entries is not None:
        requested_ids = {row.id if isinstance(row, WorldbookEntry) else row for row in entries}
        selected_entries = list(
            worldbook.entries.filter(id__in=requested_ids).prefetch_related(
                "categories", "scoped_characters", "scoped_conversations"
            )
        )
        if len(selected_entries) != len(requested_ids):
            raise ValueError("entry does not belong to worldbook")
        included_category_ids = {
            category_id
            for entry in selected_entries
            for category_id in entry.categories.values_list("id", flat=True)
        }
        by_id = {row.id: row for row in all_categories}
        for category_id in list(included_category_ids):
            node = by_id.get(category_id)
            while node and node.parent_id is not None:
                included_category_ids.add(node.parent_id)
                node = by_id.get(node.parent_id)
        roots = [row for row in all_categories if row.id in included_category_ids and row.parent_id not in included_category_ids]
        roots.sort(key=lambda row: (row.position, row.created_at, str(row.id)))
        ordered_categories = [row for row in _category_preorder(all_categories, roots) if row.id in included_category_ids]
    else:
        included_category_ids = {row.id for row in all_categories}
        roots = [row for row in all_categories if row.parent_id is None]
        roots.sort(key=lambda row: (row.position, row.created_at, str(row.id)))
        ordered_categories = _category_preorder(all_categories, roots)
        selected_entries = list(
            worldbook.entries.prefetch_related("categories", "scoped_characters", "scoped_conversations").all()
        )

    category_rows = [
        {
            "id": _source_id(row.id),
            "name": row.name,
            "parent_id": _source_id(row.parent_id) if row.parent_id in included_category_ids else None,
            "position": row.position,
        }
        for row in ordered_categories
    ]
    entry_rows = []
    for row in selected_entries:
        entry_rows.append(
            {
                "id": _source_id(row.id),
                "name": row.name,
                "content": row.content,
                "enabled": row.enabled,
                "trigger_mode": row.trigger_mode,
                "keywords": list(row.keywords),
                "insertion_position": row.insertion_position,
                "priority": row.priority,
                "scope_type": row.scope_type,
                "category_ids": [
                    _source_id(value)
                    for value in row.categories.values_list("id", flat=True)
                    if value in included_category_ids
                ],
                "scoped_character_ids": [_source_id(value) for value in row.scoped_characters.values_list("id", flat=True)],
                "scoped_conversation_ids": [
                    _source_id(value) for value in row.scoped_conversations.values_list("id", flat=True)
                ],
                "import_metadata": row.import_metadata,
            }
        )
    return {
        "format": FORMAT,
        "version": VERSION,
        "worldbook": {"name": worldbook.name, "description": worldbook.description, "enabled": worldbook.enabled},
        "categories": category_rows,
        "entries": entry_rows,
    }


def _decode_payload(payload):
    if isinstance(payload, bytes):
        if len(payload) > MAX_LOGICAL_BYTES:
            raise ImportValidationError("payload exceeds logical size limit")
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImportValidationError("payload is not valid UTF-8") from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_LOGICAL_BYTES:
            raise ImportValidationError("payload exceeds logical size limit")
        try:
            payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ImportValidationError("payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ImportValidationError("payload must be a JSON object")
    try:
        logical_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ImportValidationError("payload is not JSON-compatible") from exc
    if logical_size > MAX_LOGICAL_BYTES:
        raise ImportValidationError("payload exceeds logical size limit")
    return payload


def _text(value, field_name, *, maximum, required=False, default=""):
    if value is None and not required:
        return default
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ImportValidationError(f"invalid {field_name}")
    return value.strip() if field_name == "name" else value


def _boolean(value, field_name, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ImportValidationError(f"invalid {field_name}")
    return value


def _integer(value, field_name, default=0):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ImportValidationError(f"invalid {field_name}")
    return value


def _string_list(value, field_name, *, maximum=5000):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum or not all(isinstance(item, str) for item in value):
        raise ImportValidationError(f"invalid {field_name}")
    return list(value)


def _normalize_native(payload):
    if payload.get("format") not in (None, FORMAT) or payload.get("version") not in (None, VERSION):
        raise ImportValidationError("unsupported native format or version")
    book = payload.get("worldbook")
    if isinstance(book, dict):
        categories = payload.get("categories", book.get("categories", []))
        entries = payload.get("entries", book.get("entries", []))
    else:
        categories = payload.get("categories", [])
        entries = payload.get("entries", [])
    if not isinstance(book, dict) or not isinstance(categories, list) or not isinstance(entries, list):
        raise ImportValidationError("invalid native structure")
    if len(categories) > MAX_CATEGORIES:
        raise ImportValidationError("more than 5,000 categories")
    if len(entries) > MAX_ENTRIES:
        raise ImportValidationError("more than 5,000 entries")

    normalized_book = {
        "name": _text(book.get("name"), "name", maximum=120, required=True),
        "description": _text(book.get("description"), "description", maximum=MAX_CONTENT_LENGTH),
        "enabled": _boolean(book.get("enabled"), "enabled", True),
    }
    normalized_categories = []
    category_ids = set()
    for row in categories:
        if not isinstance(row, dict) or "id" not in row:
            raise ImportValidationError("invalid category")
        source_id = _source_id(row["id"])
        if source_id in category_ids:
            raise ImportValidationError("duplicate category reference")
        category_ids.add(source_id)
        parent_id = row.get("parent_id")
        normalized_categories.append(
            {
                "id": source_id,
                "name": _text(row.get("name"), "category name", maximum=120, required=True),
                "parent_id": _source_id(parent_id) if parent_id is not None else None,
                "position": _integer(row.get("position"), "category position"),
            }
        )
    parent_by_id = {row["id"]: row["parent_id"] for row in normalized_categories}
    for source_id, parent_id in parent_by_id.items():
        if parent_id is not None and parent_id not in parent_by_id:
            raise ImportValidationError("unknown category parent reference")
        seen = set()
        node = source_id
        depth = 0
        while node is not None:
            if node in seen:
                raise ImportValidationError("category cycle detected")
            seen.add(node)
            depth += 1
            if depth > MAX_DEPTH:
                raise ImportValidationError("category depth exceeds 50")
            node = parent_by_id.get(node)

    normalized_entries = []
    entry_ids = set()
    valid_triggers = {choice[0] for choice in WorldbookEntry.TRIGGER_CHOICES}
    valid_positions = {choice[0] for choice in WorldbookEntry.POSITION_CHOICES}
    valid_scopes = {choice[0] for choice in WorldbookEntry.SCOPE_CHOICES}
    for row in entries:
        if not isinstance(row, dict) or "id" not in row:
            raise ImportValidationError("invalid entry")
        source_id = _source_id(row["id"])
        if source_id in entry_ids:
            raise ImportValidationError("duplicate entry reference")
        entry_ids.add(source_id)
        category_refs = _string_list(row.get("category_ids"), "category references")
        if any(value not in category_ids for value in category_refs):
            raise ImportValidationError("unknown category membership reference")
        trigger_mode = row.get("trigger_mode", WorldbookEntry.TRIGGER_KEYWORD)
        position = row.get("insertion_position", WorldbookEntry.POSITION_BEFORE_CHARACTER)
        scope = row.get("scope_type", WorldbookEntry.SCOPE_GLOBAL)
        if trigger_mode not in valid_triggers or position not in valid_positions or scope not in valid_scopes:
            raise ImportValidationError("invalid entry mode")
        metadata = row.get("import_metadata", {})
        if not isinstance(metadata, dict):
            raise ImportValidationError("invalid import metadata")
        normalized_entries.append(
            {
                "id": source_id,
                "name": _text(row.get("name"), "entry name", maximum=160, required=True),
                "content": _text(row.get("content"), "entry content", maximum=MAX_CONTENT_LENGTH),
                "enabled": _boolean(row.get("enabled"), "entry enabled", True),
                "trigger_mode": trigger_mode,
                "keywords": _string_list(row.get("keywords"), "keywords"),
                "insertion_position": position,
                "priority": _integer(row.get("priority"), "priority"),
                "scope_type": scope,
                "category_ids": category_refs,
                "scoped_character_ids": _string_list(row.get("scoped_character_ids"), "character references"),
                "scoped_conversation_ids": _string_list(row.get("scoped_conversation_ids"), "conversation references"),
                "import_metadata": metadata,
            }
        )
    return ImportPreview("native", normalized_book, normalized_categories, normalized_entries)


def _silly_entries(value):
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        return [(str(index), row) for index, row in enumerate(value)]
    raise ImportValidationError("invalid SillyTavern entries")


def _silly_container(payload):
    candidates = [payload]
    for key in ("data", "worldbook", "world_info", "lorebook"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in reversed(candidates):
        if "entries" in candidate and isinstance(candidate.get("entries"), (dict, list)):
            return candidate
    return payload


def _silly_name(payload, container):
    for candidate in (container, payload, payload.get("worldbook"), payload.get("data")):
        if not isinstance(candidate, dict):
            continue
        for key in ("name", "worldbook_name", "title"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return "Imported SillyTavern Worldbook"


def _normalize_sillytavern(payload):
    container = _silly_container(payload)
    source_entries = _silly_entries(container.get("entries", {}))
    if len(source_entries) > MAX_ENTRIES:
        raise ImportValidationError("more than 5,000 entries")
    book = {
        "name": _text(_silly_name(payload, container), "name", maximum=120, required=True),
        "description": _text(container.get("description", payload.get("description")), "description", maximum=MAX_CONTENT_LENGTH),
        "enabled": True,
    }
    known = {"uid", "key", "keysecondary", "content", "disable", "constant", "selective", "order", "position", "comment"}
    positions = {
        0: WorldbookEntry.POSITION_BEFORE_CHARACTER,
        1: WorldbookEntry.POSITION_AFTER_CHARACTER,
        2: WorldbookEntry.POSITION_BEFORE_RECENT,
        3: WorldbookEntry.POSITION_BEFORE_RECENT,
        4: WorldbookEntry.POSITION_BEFORE_RECENT,
        5: WorldbookEntry.POSITION_BEFORE_RECENT,
        6: WorldbookEntry.POSITION_BEFORE_RECENT,
    }
    normalized = []
    ids = set()
    warnings = []
    for fallback_id, row in source_entries:
        if not isinstance(row, dict):
            raise ImportValidationError("invalid SillyTavern entry")
        source_id = _source_id(row.get("uid", fallback_id))
        if source_id in ids:
            raise ImportValidationError("duplicate entry reference")
        ids.add(source_id)
        primary = _string_list(row.get("key"), "SillyTavern keys")
        secondary = _string_list(row.get("keysecondary"), "SillyTavern secondary keys")
        _boolean(row.get("selective"), "selective", False)
        comment = row.get("comment")
        name = comment if isinstance(comment, str) and comment.strip() else f"Entry {source_id}"
        position_value = row.get("position", 0)
        position = positions.get(position_value, WorldbookEntry.POSITION_BEFORE_RECENT)
        if position_value not in positions:
            warnings.append(f"Entry {source_id} uses unsupported position {position_value}; mapped to before_recent_messages.")
        metadata = {
            key: value
            for key, value in row.items()
            if key not in known and (value is None or isinstance(value, (str, int, float, bool)))
        }
        normalized.append(
            {
                "id": source_id,
                "name": _text(name, "entry name", maximum=160, required=True),
                "content": _text(row.get("content"), "entry content", maximum=MAX_CONTENT_LENGTH),
                "enabled": not _boolean(row.get("disable"), "disable", False),
                "trigger_mode": WorldbookEntry.TRIGGER_ALWAYS if _boolean(row.get("constant"), "constant", False) else WorldbookEntry.TRIGGER_KEYWORD,
                "keywords": list(dict.fromkeys(primary + secondary)),
                "insertion_position": position,
                "priority": _integer(row.get("order"), "order"),
                "scope_type": WorldbookEntry.SCOPE_GLOBAL,
                "category_ids": [],
                "scoped_character_ids": [],
                "scoped_conversation_ids": [],
                "import_metadata": metadata,
            }
        )
    return ImportPreview("sillytavern", book, [], normalized, warnings)


def parse_import(payload, source_format):
    payload = _decode_payload(payload)
    normalized_format = str(source_format).strip().lower().replace("_", "-")
    native_shape = (
        isinstance(payload.get("worldbook"), dict)
        and isinstance(payload.get("categories", payload["worldbook"].get("categories", [])), list)
        and isinstance(payload.get("entries", payload["worldbook"].get("entries", [])), list)
    )
    if normalized_format in {"native", FORMAT} or native_shape:
        return _normalize_native(payload)
    if normalized_format in {"sillytavern", "silly-tavern", "st"}:
        return _normalize_sillytavern(payload)
    raise ImportValidationError("unsupported source format")


def _available_name(owner, requested):
    if not Worldbook.objects.filter(owner=owner, name=requested).exists():
        return requested
    suffix = 2
    while True:
        candidate = f"{requested} ({suffix})"
        if len(candidate) > 120:
            candidate = f"{requested[: 120 - len(str(suffix)) - 3]} ({suffix})"
        if not Worldbook.objects.filter(owner=owner, name=candidate).exists():
            return candidate
        suffix += 1


def commit_import(preview, owner, *, conflict_policy="keep_both"):
    if not isinstance(preview, ImportPreview):
        raise ImportValidationError("invalid import preview")
    if conflict_policy not in {"keep_both", "replace", "skip"}:
        raise ImportValidationError("invalid conflict policy")

    with transaction.atomic():
        existing = Worldbook.objects.filter(owner=owner, name=preview.worldbook["name"]).first()
        if existing and conflict_policy == "skip":
            return existing
        name = preview.worldbook["name"]
        if existing and conflict_policy == "replace":
            existing.delete()
        elif existing:
            name = _available_name(owner, name)

        worldbook = Worldbook(
            owner=owner,
            name=name,
            description=preview.worldbook["description"],
            enabled=preview.worldbook["enabled"],
        )
        worldbook.full_clean()
        worldbook.save()

        rows = {row["id"]: row for row in preview.categories}
        created_categories = {}

        def create_category(source_id):
            if source_id in created_categories:
                return created_categories[source_id]
            row = rows[source_id]
            parent = create_category(row["parent_id"]) if row["parent_id"] is not None else None
            category = WorldbookCategory(
                worldbook=worldbook,
                parent=parent,
                name=row["name"],
                position=row["position"],
            )
            category.save()
            created_categories[source_id] = category
            return category

        for source_id in rows:
            create_category(source_id)

        for row in preview.entries:
            entry = WorldbookEntry(
                worldbook=worldbook,
                name=row["name"],
                content=row["content"],
                enabled=row["enabled"],
                trigger_mode=row["trigger_mode"],
                keywords=row["keywords"],
                insertion_position=row["insertion_position"],
                priority=row["priority"],
                scope_type=row["scope_type"],
                import_metadata=row["import_metadata"],
            )
            entry.full_clean()
            entry.save()
            entry.categories.set(created_categories[value] for value in row["category_ids"])
            characters = list(Character.objects.filter(owner=owner, id__in=row["scoped_character_ids"]))
            conversations = list(Conversation.objects.filter(owner=owner, id__in=row["scoped_conversation_ids"]))
            if len(characters) != len(set(row["scoped_character_ids"])):
                preview.warnings.append(f"Entry {row['id']} omitted unknown character scope references.")
            if len(conversations) != len(set(row["scoped_conversation_ids"])):
                preview.warnings.append(f"Entry {row['id']} omitted unknown conversation scope references.")
            entry.scoped_characters.set(characters)
            entry.scoped_conversations.set(conversations)
        return worldbook
