from .models import ConversationWorldbookConfig, Worldbook, WorldbookCategory, WorldbookEntry


POSITION_ORDER = {
    WorldbookEntry.POSITION_BEFORE_CHARACTER: 0,
    WorldbookEntry.POSITION_AFTER_CHARACTER: 1,
    WorldbookEntry.POSITION_BEFORE_RECENT: 2,
}


def _normalized_keywords(value):
    if not isinstance(value, list):
        return []
    return [item.strip().casefold() for item in value if isinstance(item, str) and item.strip()]


def _category_states(categories, enabled_worldbook_ids, enabled_ids, excluded_ids):
    by_id = {item.id: item for item in categories}
    memo = {}

    def state(category):
        if category.id in memo:
            return memo[category.id]
        chain = []
        node = category
        seen = set()
        while node and node.id not in seen:
            chain.append(node)
            seen.add(node.id)
            node = by_id.get(node.parent_id)
        active = category.worldbook_id in enabled_worldbook_ids
        for ancestor in reversed(chain):
            if ancestor.id in excluded_ids:
                active = False
            elif ancestor.id in enabled_ids:
                active = True
        memo[category.id] = active
        return active

    return {item.id: state(item) for item in categories}


def _entry_matches(entry, *, conversation, actor, recent_text, manual_ids):
    if not entry.enabled:
        return False
    if entry.scope_type == WorldbookEntry.SCOPE_CHARACTER:
        if actor is None or actor.id not in {item.id for item in entry.scoped_characters.all()}:
            return False
    elif entry.scope_type == WorldbookEntry.SCOPE_CONVERSATION:
        if conversation is None or conversation.id not in {item.id for item in entry.scoped_conversations.all()}:
            return False
    if entry.trigger_mode == WorldbookEntry.TRIGGER_ALWAYS:
        return True
    if entry.trigger_mode == WorldbookEntry.TRIGGER_MANUAL:
        return entry.id in manual_ids
    haystack = (recent_text or "").casefold()
    return any(keyword in haystack for keyword in _normalized_keywords(entry.keywords))


def _resolve(*, owner, recent_text, conversation=None, actor=None, config=None, diagnostics=None):
    if config is None:
        enabled_worldbook_ids = set(Worldbook.objects.filter(owner=owner, enabled=True).values_list("id", flat=True))
        enabled_category_ids = set()
        excluded_category_ids = set()
        excluded_entry_ids = set()
        manual_entry_ids = set()
        manual_entries = []
        enabled_categories = []
    else:
        enabled_worldbook_ids = {item.id for item in config.enabled_worldbooks.all() if item.enabled}
        enabled_categories = list(config.enabled_categories.all())
        enabled_category_ids = {item.id for item in enabled_categories}
        excluded_category_ids = {item.id for item in config.excluded_categories.all()}
        excluded_entry_ids = {item.id for item in config.excluded_entries.all()}
        manual_entries = list(config.manual_entries.all())
        manual_entry_ids = {item.id for item in manual_entries}

    relevant_worldbook_ids = set(enabled_worldbook_ids)
    relevant_worldbook_ids.update(item.worldbook_id for item in enabled_categories)
    relevant_worldbook_ids.update(item.worldbook_id for item in manual_entries if item.worldbook.enabled)
    categories = list(WorldbookCategory.objects.filter(worldbook_id__in=relevant_worldbook_ids))
    active_categories = _category_states(
        categories,
        enabled_worldbook_ids,
        enabled_category_ids,
        excluded_category_ids,
    )

    entries = list(
        WorldbookEntry.objects.filter(worldbook__owner=owner)
        .select_related("worldbook")
        .prefetch_related("categories", "scoped_characters", "scoped_conversations")
    )
    result = []
    for entry in entries:
        reason=''
        category_ids = {item.id for item in entry.categories.all()}
        included = any(active_categories.get(category_id, False) for category_id in category_ids)
        if not category_ids and entry.worldbook_id in enabled_worldbook_ids:
            included = True
        if entry.id in manual_entry_ids:
            included = True
        if not entry.worldbook.enabled: reason='整本世界书已禁用'
        elif entry.id in excluded_entry_ids: reason='对话明确排除此条目'
        elif not entry.enabled: reason='条目已禁用'
        elif not included: reason='分类未启用或已排除' if category_ids else '世界书未选用'
        elif entry.scope_type==WorldbookEntry.SCOPE_CHARACTER and (actor is None or actor.pk not in {row.pk for row in entry.scoped_characters.all()}): reason='适用角色范围不符'
        elif entry.scope_type==WorldbookEntry.SCOPE_CONVERSATION and (conversation is None or conversation.pk not in {row.pk for row in entry.scoped_conversations.all()}): reason='适用对话范围不符'
        elif entry.trigger_mode==WorldbookEntry.TRIGGER_MANUAL and entry.pk not in manual_entry_ids: reason='未手动选择此条目'
        elif entry.trigger_mode==WorldbookEntry.TRIGGER_KEYWORD and not any(key in (recent_text or '').casefold() for key in _normalized_keywords(entry.keywords)): reason='关键词未出现在近期公开消息中'
        if not reason and included and _entry_matches(
            entry,
            conversation=conversation,
            actor=actor,
            recent_text=recent_text,
            manual_ids=manual_entry_ids,
        ):
            result.append(entry)
        elif diagnostics is not None:
            diagnostics.append({'id':str(entry.pk),'name':entry.name,'reason':reason or '触发条件未满足'})
    result.sort(key=lambda item: (POSITION_ORDER[item.insertion_position], -item.priority, item.created_at, str(item.id)))
    return result


def resolve_worldbook_entries(conversation, actor, recent_text,*,diagnostics=None):
    try:
        config = ConversationWorldbookConfig.objects.prefetch_related(
            "enabled_worldbooks",
            "enabled_categories",
            "excluded_categories",
            "excluded_entries",
            "manual_entries",
        ).get(conversation=conversation)
    except ConversationWorldbookConfig.DoesNotExist:
        if diagnostics is not None:
            diagnostics.extend({'id':str(entry.pk),'name':entry.name,'reason':'尚未选择对话世界书'} for entry in WorldbookEntry.objects.filter(worldbook__owner=conversation.owner))
        return []
    return _resolve(
        owner=conversation.owner,
        recent_text=recent_text,
        conversation=conversation,
        actor=actor,
        config=config,
        diagnostics=diagnostics,
    )


def preview_worldbook_entries(user, text, *, conversation=None, actor=None):
    if conversation is not None:
        if conversation.owner_id != user.id:
            return []
        return resolve_worldbook_entries(conversation, actor, text)
    return _resolve(owner=user, recent_text=text, actor=actor)
