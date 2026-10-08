import hashlib
import json
import uuid
from types import SimpleNamespace

from .models import Character, Worldbook, WorldbookEntry

EXTENDED_FIELDS=('scenario','first_mes','alternate_greetings','character_worldbook','context_policy')


def sync_lore_from_book(book):
    """管理页的编辑同步回素材；已创建故事仍持有自己的原文快照。"""
    entries=[]
    for index,row in enumerate(book.entries.order_by('created_at','id')):
        entries.append({'name':row.name,'content':row.content,'enabled':row.enabled,'trigger_mode':row.trigger_mode,
            'keywords':row.keywords,'priority':row.priority,'insertion_position':row.insertion_position})
        row.import_metadata={**row.import_metadata,'card_index':index}
        WorldbookEntry.objects.filter(pk=row.pk).update(import_metadata=row.import_metadata)
    for character in Character.objects.filter(personal_worldbook=book):
        raw=character.character_worldbook
        raw={**raw,'entries':entries} if isinstance(raw,dict) else entries
        digest=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
        Character.objects.filter(pk=character.pk).update(character_worldbook=raw,lore_hash=digest)


def lore_entries(raw):
    if not isinstance(raw,(dict,list)):
        raise ValueError('角色专属世界书必须为对象或数组')
    entries=raw.get('entries',[]) if isinstance(raw,dict) else raw
    if isinstance(entries,dict):
        entries=list(entries.values())
    if not isinstance(entries,list) or len(entries)>5000:
        raise ValueError('角色专属世界书条目格式无效')
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('content'),str) or not entry['content'].strip() or len(entry['content'])>100000:
            raise ValueError('角色专属世界书正文无效')
        if 'name' in entry and (not isinstance(entry['name'],str) or len(entry['name'])>160):
            raise ValueError('角色专属世界书条目名称无效')
        mode=entry.get('trigger_mode','always' if entry.get('constant') else 'keyword')
        if mode not in ('always','keyword','manual'):
            raise ValueError('角色专属世界书触发方式无效')
        keys=entry.get('keywords',entry.get('keys',entry.get('key',[])))
        if not isinstance(keys,list) or any(not isinstance(key,str) or not key.strip() or len(key)>200 for key in keys):
            raise ValueError('角色专属世界书关键词无效')
        if entry.get('insertion_position','before_character') not in ('before_character','after_character','before_recent_messages'):
            raise ValueError('角色专属世界书插入位置无效')
        if type(entry.get('priority',0)) is not int:
            raise ValueError('角色专属世界书优先级必须为整数')
    return entries


def validate_extended_fields(character):
    from .context_selection import validate_context_policy
    validate_context_policy(character.context_policy)
    for key in ('scenario','first_mes'):
        if not isinstance(getattr(character,key),str) or len(getattr(character,key))>100000:
            raise ValueError('角色场景或开场白不能超过 100,000 字')
    greetings=character.alternate_greetings
    if not isinstance(greetings,list) or len(greetings)>100 or any(not isinstance(item,str) or len(item)>100000 for item in greetings):
        raise ValueError('备用开场白必须为最多 100 项的文字数组')
    lore_entries(character.character_worldbook)
    if len(json.dumps(character.character_worldbook,ensure_ascii=False))>500000:
        raise ValueError('角色专属世界书内容过大')


def sync_character_lore(character):
    raw=character.character_worldbook
    digest=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
    if digest==character.lore_hash:
        return
    entries=lore_entries(raw)
    book=character.personal_worldbook
    if book and book.owner_id!=character.owner_id:
        raise ValueError('角色专属世界书不属于当前账号')
    if not entries and book:
        book.entries.all().delete()
    if entries and book is None:
        original=f'{character.name}的专属世界书'
        name=original[:120]
        index=1
        while Worldbook.objects.filter(owner=character.owner,name=name).exists():
            index+=1
            suffix=f'（{index}）'
            name=original[:120-len(suffix)]+suffix
        book=Worldbook.objects.create(owner=character.owner,name=name)
    if entries:
        existing={row.import_metadata.get('card_index'):row for row in book.entries.all() if isinstance(row.import_metadata,dict)}
        used=set()
        retained=[]
        for index,entry in enumerate(entries):
            name=entry.get('name') or entry.get('comment') or f'条目{index+1}'
            name=str(name)[:160]
            if name in used:
                name=f'{name[:145]}（{index+1}）'
            used.add(name)
            keys=entry.get('keywords',entry.get('keys',entry.get('key',[])))
            row=existing.get(index)
            if row is None:
                row=book.entries.filter(name=name).first() or WorldbookEntry(worldbook=book)
            row.name=name
            row.content=entry['content']
            row.enabled=entry.get('enabled',not entry.get('disable',False)) is not False
            row.trigger_mode=entry.get('trigger_mode','always' if entry.get('constant') else 'keyword')
            row.keywords=keys
            row.scope_type='character'
            row.priority=entry.get('priority',0)
            row.insertion_position=entry.get('insertion_position','before_character')
            row.import_metadata={'角色卡专属':True,'card_index':index}
            row.save()
            row.scoped_characters.set([character])
            retained.append(row.pk)
        book.entries.exclude(pk__in=retained).delete()
    Character.objects.filter(pk=character.pk).update(personal_worldbook=book,lore_hash=digest)
    character.personal_worldbook=book
    character.lore_hash=digest


def resolve_personal_lore(conversation,actor,recent_text):
    """采用对话中冻结的角色原文，按三种触发方式选择专属条目。"""
    if not actor.personal_worldbook or not actor.personal_worldbook.enabled:
        return []
    rows=list(actor.personal_worldbook.entries.all())
    by_index={row.import_metadata.get('card_index'):row for row in rows if isinstance(row.import_metadata,dict)}
    manual=set()
    from .models import ConversationWorldbookConfig
    config=ConversationWorldbookConfig.objects.filter(conversation=conversation).first()
    if config:
        manual=set(config.manual_entries.values_list('id',flat=True))
    # 专属书隐式选中，但条目禁用、分类排除、作用域和手动触发仍由通用解析器决定。
    from .worldbook_resolver import _resolve
    class Values:
        def __init__(self,values): self.values=values
        def all(self): return self.values
    settings=SimpleNamespace(**{key:Values(list(getattr(config,key).all()) if config else [])
        for key in ('enabled_worldbooks','enabled_categories','excluded_categories','excluded_entries','manual_entries')})
    settings.enabled_worldbooks=Values([*settings.enabled_worldbooks.all(),actor.personal_worldbook])
    allowed={entry.pk for entry in _resolve(owner=conversation.owner,recent_text=recent_text,conversation=conversation,actor=actor,config=settings)}
    result=[]
    for index,entry in enumerate(lore_entries(actor.character_worldbook)):
        name=entry.get('name') or entry.get('comment') or f'条目{index+1}'
        row=next((row for row in rows if str(row.pk)==entry.get('_entry_id')),None) if entry.get('_entry_id') else by_index.get(index)
        if row is None or row.pk not in allowed:
            continue
        result.append(SimpleNamespace(id=row.pk,name=name,content=entry['content'],priority=row.priority,
            created_at=row.created_at,insertion_position=row.insertion_position,
            trigger_mode=row.trigger_mode,keywords=row.keywords,scope_type=row.scope_type,worldbook_id=actor.personal_worldbook_id))
    return result
