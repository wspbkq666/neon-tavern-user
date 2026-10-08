"""可恢复的故事快照；历史消息只在有边界状态证据时允许存档。"""
import copy
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (Character, Conversation, ConversationParticipant, ConversationActorState, Message,
                     StoryCheckpoint, GenerationJob, Worldbook, WorldbookCategory, WorldbookEntry,
                     ConversationWorldbookConfig, StoryMemoryRevision,GenerationContextTrace)
from .story_state import ensure_story_state, state_payload, StoryConflict
from .worldbook_transfer import export_native

CAPTURE_DISABLED=ContextVar('story_capture_disabled',default=False)
CONFIG_FIELDS=('enabled_worldbooks','enabled_categories','excluded_categories','excluded_entries','manual_entries')
ENTRY_FIELDS=('name','content','enabled','trigger_mode','keywords','insertion_position','priority','scope_type','import_metadata')
GENERATION_FIELDS=('temperature','top_p','max_tokens','context_capacity','stream_output','strict_persona','auto_state_extraction','reply_format','memory_threshold','profile_threshold')


@contextmanager
def suspend_capture():
    token=CAPTURE_DISABLED.set(True)
    try:
        yield
    finally:
        CAPTURE_DISABLED.reset(token)


def capture_context(conversation):
    conversation.refresh_from_db()
    actors=[]
    for link in conversation.conversationparticipant_set.select_related('character').order_by('position'):
        state=ensure_story_state(conversation,link.character)
        actors.append({'id':str(link.character_id),'position':link.position,'template':copy.deepcopy(state.template_snapshot),
                       'state':state_payload(state)})
    config=ConversationWorldbookConfig.objects.filter(conversation=conversation).first()
    selection={key:[] for key in CONFIG_FIELDS}
    books=[]
    ids={str(link.character.personal_worldbook_id) for link in conversation.conversationparticipant_set.select_related('character') if link.character.personal_worldbook_id}
    if config:
        selection={key:[str(pk) for pk in getattr(config,key).values_list('id',flat=True)] for key in CONFIG_FIELDS}
        ids.update(selection['enabled_worldbooks'])
        for key in CONFIG_FIELDS[1:]:
            ids.update(str(item.worldbook_id) for item in getattr(config,key).all())
    books=[{'id':str(book.pk),'payload':export_native(book)} for book in Worldbook.objects.filter(owner=conversation.owner,pk__in=ids)]
    from .settings_api import effective_values
    options={**effective_values(conversation.owner),**conversation.generation_configuration}
    return {'version':1,'source_conversation_id':str(conversation.pk),'title':conversation.title,
            'player_character_id':str(conversation.player_character_id),'environment':copy.deepcopy(conversation.environment),
            'language':conversation.language,'auto_actor_ids':conversation.auto_actor_ids,'auto_mode':conversation.auto_mode,
            'generation_configuration':{key:options[key] for key in GENERATION_FIELDS},
            'long_memory':conversation.long_memory,'locked_facts':copy.deepcopy(conversation.locked_facts),
            'memory_message_count':conversation.memory_message_count,'profile_message_count':conversation.profile_message_count,
            'actors':actors,'worldbook_selection':selection,'worldbooks':books}


@receiver(post_save,sender=Message,dispatch_uid='story_boundary_capture')
def capture_message_boundary(sender,instance,created,raw=False,**kwargs):
    if not created or raw or CAPTURE_DISABLED.get() or not instance.conversation.participants.exists():
        return
    snapshot=capture_context(instance.conversation)
    Message.objects.filter(pk=instance.pk).update(story_snapshot=snapshot)
    instance.story_snapshot=snapshot


def serialize_story(conversation,through_message_id=None):
    rows=list(conversation.messages.order_by('created_at','id'))
    if through_message_id:
        index=next((index for index,row in enumerate(rows) if str(row.pk)==str(through_message_id)),None)
        if index is None:
            raise ValueError('消息不属于当前对话')
        rows=rows[:index+1]
        context=copy.deepcopy(rows[-1].story_snapshot)
        if not context:
            raise ValueError('此旧节点缺少历史状态记录，无法创建精确存档')
    else:
        context=capture_context(conversation)
    from .generation_trace import public_context_trace
    traces=GenerationContextTrace.objects.filter(pk__in=[row.context_trace_id for row in rows if row.context_trace_id],job__requested_by=conversation.owner).select_related('job')
    return {**context,'source_traces':[public_context_trace(trace) for trace in traces],'messages':[{'id':str(row.pk),'speaker_id':str(row.speaker_id) if row.speaker_id else None,
            'context_trace_id':str(row.context_trace_id) if row.context_trace_id else None,
            'kind':row.kind,'content':row.content,'source':row.source,'consumed_by':row.consumed_by,
            'state_snapshot':row.state_snapshot,'story_snapshot':row.story_snapshot,'created_at':row.created_at.isoformat()}
           for row in rows]}


def lock_story(conversation,expected_revision=None):
    query=Conversation.objects.filter(pk=conversation.pk)
    if expected_revision is not None:
        if type(expected_revision) is not int:
            raise ValueError('对话版本无效')
        query=query.filter(story_revision=expected_revision)
    if not query.update(story_revision=F('story_revision')):
        raise StoryConflict('对话已变化，请重新查看存档预览')
    conversation.refresh_from_db()


@transaction.atomic
def create_checkpoint(conversation,name,through_message_id=None):
    if not isinstance(name,str) or not name.strip() or len(name)>120:
        raise ValueError('存档名称不能为空或超过 120 字')
    lock_story(conversation)
    return StoryCheckpoint.objects.create(conversation=conversation,name=name.strip(),snapshot=serialize_story(conversation,through_message_id))


def remap(value,mapping):
    if isinstance(value,str):
        return mapping.get(value,value)
    if isinstance(value,list):
        return [remap(item,mapping) for item in value]
    if isinstance(value,dict):
        return {mapping.get(key,key):remap(item,mapping) for key,item in value.items()}
    return value


def remap_boundary(snapshot,mapping):
    """只重映射引用字段，原文即使恰好等于编号也不改写。"""
    value=copy.deepcopy(snapshot)
    if not value:
        return value
    for key in ('source_conversation_id','player_character_id','auto_actor_ids'):
        value[key]=remap(value[key],mapping)
    value['worldbook_selection']=remap(value['worldbook_selection'],mapping)
    for actor in value['actors']:
        actor['id']=mapping.get(actor['id'],actor['id'])
        actor['state']['character_id']=actor['id']
        template=actor['template']
        if template.get('_personal_worldbook_id'):
            template['_personal_worldbook_id']=remap(template['_personal_worldbook_id'],mapping)
        from .character_lore import lore_entries
        for entry in lore_entries(template.get('character_worldbook',{})):
            if entry.get('_entry_id'):
                entry['_entry_id']=remap(entry['_entry_id'],mapping)
    for book in value['worldbooks']:
        book['id']=mapping.get(book['id'],book['id'])
        for category in book['payload']['categories']:
            category['id']=mapping.get(category['id'],category['id'])
            if category['parent_id']:
                category['parent_id']=mapping.get(category['parent_id'],category['parent_id'])
        for entry in book['payload']['entries']:
            for key in ('id','category_ids','scoped_character_ids','scoped_conversation_ids'):
                entry[key]=remap(entry[key],mapping)
    return value


def restore_worldbooks(conversation,snapshot,mapping):
    config,_=ConversationWorldbookConfig.objects.get_or_create(conversation=conversation)
    for item in snapshot['worldbooks']:
        payload=item['payload']
        definition=payload['worldbook']
        name=definition['name']
        count=1
        while Worldbook.objects.filter(owner=conversation.owner,name=name).exists():
            count+=1
            suffix=f'（存档副本{count}）'
            name=definition['name'][:120-len(suffix)]+suffix
        book=Worldbook.objects.create(owner=conversation.owner,name=name,description=definition['description'],enabled=definition['enabled'])
        mapping[item['id']]=str(book.pk)
        categories={}
        for category in payload['categories']:
            parent=categories.get(category['parent_id'])
            if category['parent_id'] and parent is None:
                raise ValueError('存档世界书分类引用无效')
            row=WorldbookCategory.objects.create(worldbook=book,parent=parent,name=category['name'],position=category['position'])
            categories[category['id']]=row
            mapping[category['id']]=str(row.pk)
        for entry in payload['entries']:
            row=WorldbookEntry.objects.create(worldbook=book,**{key:copy.deepcopy(entry[key]) for key in ENTRY_FIELDS})
            row.categories.set([categories[pk] for pk in entry['category_ids']])
            actor_ids=remap(entry['scoped_character_ids'],mapping)
            actors=list(Character.objects.filter(owner=conversation.owner,pk__in=actor_ids))
            if len(actors)!=len(set(actor_ids)):
                raise ValueError('存档角色范围引用已失效')
            row.scoped_characters.set(actors)
            conversation_ids=remap(entry['scoped_conversation_ids'],mapping)
            scoped=list(Conversation.objects.filter(owner=conversation.owner,pk__in=conversation_ids))
            if len(scoped)!=len(set(conversation_ids)):
                raise ValueError('存档对话范围引用已失效')
            row.scoped_conversations.set(scoped)
            mapping[entry['id']]=str(row.pk)
    for key in CONFIG_FIELDS:
        getattr(config,key).set(remap(snapshot['worldbook_selection'][key],mapping))


def populate_story(conversation,snapshot):
    if snapshot.get('version')!=1:
        raise ValueError('存档版本暂不支持')
    mapping={snapshot['source_conversation_id']:str(conversation.pk)}
    actors={str(item.pk):item for item in Character.objects.filter(owner=conversation.owner,pk__in=[row['id'] for row in snapshot['actors']])}
    if len(actors)!=len(snapshot['actors']) or snapshot['player_character_id'] not in actors:
        raise ValueError('存档角色资料已失效')
    conversation.messages.all().delete()
    conversation.actor_states.all().delete()
    conversation.conversationparticipant_set.all().delete()
    for key in ('environment','language','auto_actor_ids','auto_mode','long_memory','locked_facts','memory_message_count','profile_message_count'):
        setattr(conversation,key,copy.deepcopy(snapshot[key]))
    conversation.generation_configuration={key:value for key,value in snapshot.get('generation_configuration',{}).items() if key in GENERATION_FIELDS}
    conversation.player_character=actors[snapshot['player_character_id']]
    conversation.auto_generate=False
    conversation.story_revision+=1
    conversation.memory_revision+=1
    conversation.memory_summary_status='idle'
    conversation.memory_summary_error=''
    conversation.save()
    StoryMemoryRevision.objects.create(conversation=conversation,revision=conversation.memory_revision,
        text=conversation.long_memory,locked_facts=conversation.locked_facts,source='存档恢复',message_count=conversation.memory_message_count)
    for item in snapshot['actors']:
        actor=actors[item['id']]
        ConversationParticipant.objects.create(conversation=conversation,character=actor,position=item['position'])
        state=item['state']
        ConversationActorState.objects.create(conversation=conversation,character=actor,template_snapshot=item['template'],
            state_fields=state['state_fields'],relationship_notes=state['relationship_notes'],affinity=state['affinity'],
            clothing_type=state['clothing_type'],clothing_state=state['clothing_state'],revision=state['revision']+1,
            initialization_source='存档恢复')
    restore_worldbooks(conversation,snapshot,mapping)
    restored=remap_boundary(snapshot,mapping)
    for item in restored['actors']:
        ConversationActorState.objects.filter(conversation=conversation,character_id=item['id']).update(template_snapshot=item['template'])
    from .generation_trace import CONFIGURATION_FIELDS
    for trace in snapshot.get('source_traces',[]):
        actor=actors.get(trace.get('actor_id'))
        job=GenerationJob.objects.create(conversation=conversation,requested_by=conversation.owner,actor_ids=[str(actor.pk)] if actor else [],status='done',story_revision=conversation.story_revision)
        selected=copy.deepcopy(trace['selected']);excluded=copy.deepcopy(trace['excluded'])
        for row in [*selected,*excluded]:
            if row.get('id'): row['id']=mapping.get(row['id'],row['id'])
        saved_trace=GenerationContextTrace.objects.create(job=job,actor=actor,selected=selected,excluded=excluded,
            estimated_usage=trace['estimated_usage'],configuration={key:value for key,value in trace['configuration'].items() if key in CONFIGURATION_FIELDS},
            character_snapshot=trace['character_snapshot'],content_hash=trace['content_hash'])
        GenerationContextTrace.objects.filter(pk=saved_trace.pk).update(created_at=parse_datetime(trace['created_at']))
        mapping[trace['id']]=str(saved_trace.pk)
    message_rows=[]
    with suspend_capture():
        for item in snapshot['messages']:
            speaker=actors.get(item['speaker_id'])
            if item['speaker_id'] and speaker is None:
                raise ValueError('存档消息角色引用无效')
            row=Message.objects.create(conversation=conversation,speaker=speaker,kind=item['kind'],content=item['content'],
                source=item['source'],consumed_by=remap(item['consumed_by'],mapping),state_snapshot=item['state_snapshot'],context_trace_id=mapping.get(item.get('context_trace_id')))
            mapping[item['id']]=str(row.pk)
            message_rows.append((row,item))
        for row,item in message_rows:
            Message.objects.filter(pk=row.pk).update(created_at=parse_datetime(item['created_at']),story_snapshot=remap_boundary(item['story_snapshot'],mapping))
    return mapping


@transaction.atomic
def restore_checkpoint(conversation,checkpoint,expected_revision):
    if checkpoint.conversation.owner_id!=conversation.owner_id:
        raise ValueError('存档不属于当前账号')
    lock_story(conversation,expected_revision)
    before=create_checkpoint(conversation,'回档前自动存档')
    GenerationJob.objects.filter(conversation=conversation,status__in=['queued','running']).update(
        status='cancelled',error='对话已回档，旧生成任务作废',finished_at=timezone.now())
    mapping=populate_story(conversation,copy.deepcopy(checkpoint.snapshot))
    return {'recovery_checkpoint_id':str(before.pk),'story_revision':conversation.story_revision,'mapping':mapping}


@transaction.atomic
def fork_checkpoint(owner,checkpoint,title):
    if checkpoint.conversation.owner_id!=owner.pk or not isinstance(title,str) or not title.strip() or len(title)>120:
        raise ValueError('存档归属或分支名称无效')
    player=Character.objects.filter(owner=owner,pk=checkpoint.snapshot['player_character_id']).first()
    if player is None:
        raise ValueError('存档玩家资料已失效')
    conversation=Conversation.objects.create(owner=owner,title=title.strip(),player_character=player)
    populate_story(conversation,copy.deepcopy(checkpoint.snapshot))
    return conversation
