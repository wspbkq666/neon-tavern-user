"""角色素材是模板，故事中的运行状态独立保存。"""
import copy
import json

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Conversation, ConversationActorState, GenerationJob, Message

TEMPLATE_FIELDS = ('name', 'summary', 'personality', 'speech_habits', 'memories', 'relationship_notes',
                   'state_fields', 'affinity', 'clothing_type', 'clothing_state','scenario','first_mes','alternate_greetings','character_worldbook','context_policy')
STATE_FIELDS = ('state_fields', 'relationship_notes', 'affinity', 'clothing_type', 'clothing_state')


class StoryConflict(ValueError):
    pass


def template_snapshot(character):
    snapshot={key: copy.deepcopy(getattr(character, key)) for key in TEMPLATE_FIELDS}
    if character.personal_worldbook_id:
        snapshot['_personal_worldbook_id']=str(character.personal_worldbook_id)
        from .character_lore import lore_entries
        rows={row.import_metadata.get('card_index'):row for row in character.personal_worldbook.entries.all()}
        for index,entry in enumerate(lore_entries(snapshot['character_worldbook'])):
            if index in rows:
                entry['_entry_id']=str(rows[index].pk)
    return snapshot


def ensure_story_state(conversation, character, *, recover_history=True):
    if character.owner_id != conversation.owner_id or not conversation.participants.filter(pk=character.pk).exists():
        raise ValueError('角色不属于当前对话')
    defaults = {key: copy.deepcopy(getattr(character, key)) for key in STATE_FIELDS}
    defaults['template_snapshot'] = template_snapshot(character)
    defaults['initialization_source'] = '角色卡初始设定'
    if recover_history and not conversation.actor_states.filter(character=character).exists():
        previous = conversation.messages.filter(speaker=character, kind=Message.STATE).order_by('-created_at','-id').first()
        if previous and isinstance(previous.state_snapshot, dict) and previous.state_snapshot:
            raw = copy.deepcopy(previous.state_snapshot)
            for key in ('affinity','clothing_type','clothing_state'):
                if key in raw:
                    defaults[key] = raw.pop(key)
            defaults['state_fields'] = raw
            defaults['initialization_source'] = '历史状态记录'
    return ConversationActorState.objects.get_or_create(conversation=conversation, character=character, defaults=defaults)[0]


def state_payload(state):
    return {'character_id':str(state.character_id), 'revision':state.revision,
            'initialization_source':state.initialization_source,
            **{key:copy.deepcopy(getattr(state,key)) for key in STATE_FIELDS}}


def actor_context(conversation, character):
    state = ensure_story_state(conversation, character)
    actor = copy.copy(character)
    for key,value in state.template_snapshot.items():
        if key in TEMPLATE_FIELDS:
            setattr(actor, key, copy.deepcopy(value))
    if state.template_snapshot.get('_personal_worldbook_id'):
        from .models import Worldbook
        identifier=state.template_snapshot['_personal_worldbook_id']
        actor.personal_worldbook=Worldbook.objects.filter(pk=identifier,owner=conversation.owner).first()
    for key in STATE_FIELDS:
        setattr(actor, key, copy.deepcopy(getattr(state, key)))
    return actor


def validate_state_values(values):
    if not isinstance(values, dict) or not values or set(values) - set(STATE_FIELDS):
        raise ValueError('状态字段无效')
    for key,value in values.items():
        if key in ('state_fields','relationship_notes'):
            if not isinstance(value,dict) or len(value)>50 or any(
                not isinstance(k,str) or not 1<=len(k)<=50 or len(json.dumps(v,ensure_ascii=False))>2000
                for k,v in value.items()):
                raise ValueError('状态名称或内容无效')
        elif key=='affinity':
            if type(value) is not int or not 0<=value<=100:
                raise ValueError('好感度必须为 0 到 100 的整数')
        elif not isinstance(value,str) or len(value)>200:
            raise ValueError('衣着内容不能超过 200 字')


def _save_state(state, values, expected_revision):
    if type(expected_revision) is not int:
        raise ValueError('缺少有效的状态版本')
    changed = ConversationActorState.objects.filter(pk=state.pk,revision=expected_revision).update(
        **values, revision=F('revision')+1, updated_at=timezone.now())
    if not changed:
        raise StoryConflict('状态已变化，请重新读取后再修改')
    state.refresh_from_db()
    return state


@transaction.atomic
def update_story_state(conversation, character, values, expected_revision):
    validate_state_values(values)
    state = ensure_story_state(conversation,character)
    _save_state(state,values,expected_revision)
    Conversation.objects.filter(pk=conversation.pk).update(story_revision=F('story_revision')+1)
    conversation.refresh_from_db()
    return state


@transaction.atomic
def apply_actor_result(conversation, character, result, expected_revision, *, expected_story_revision=None, job_id=None, lease_token=None):
    # 条件写入同时在 SQLite 上取得写锁，阻止手动编辑与提交交错。
    guard = Conversation.objects.filter(pk=conversation.pk)
    if expected_story_revision is not None:
        guard = guard.filter(story_revision=expected_story_revision)
    if not guard.update(story_revision=F('story_revision')):
        raise StoryConflict('对话已变化，过期生成结果未保存')
    job_filter=GenerationJob.objects.filter(pk=job_id,status='running') if job_id else None
    if job_filter is not None and lease_token is not None:
        job_filter=job_filter.filter(lease_token=lease_token,lease_until__gte=timezone.now())
    if job_filter is not None and not job_filter.exists():
        raise StoryConflict('生成已取消，结果未保存')
    state = ensure_story_state(conversation,character)
    values = {key:copy.deepcopy(getattr(state,key)) for key in STATE_FIELDS}
    from .generation import flatten_actor_state
    updates = flatten_actor_state(character,result.get('state_updates',{}))
    for name,value in updates.items():
        if isinstance(value,dict) and set(value)=={'delta'} and type(value['delta']) in (int,float):
            old = values['state_fields'].get(name,0)
            value = old+value['delta'] if type(old) in (int,float) else value['delta']
        values['state_fields'][name] = value
    for key,value in result.get('fixed_status',{}).items():
        if key=='affinity':
            values[key]=max(0,min(100,round(value)))
        elif key in ('clothing_type','clothing_state'):
            values[key]=value.strip()
    validate_state_values(values)
    return _save_state(state,values,expected_revision)


@transaction.atomic
def apply_template_update(conversation,character,expected_revision,*,reset_state=False,selected_fields=None):
    state=ensure_story_state(conversation,character)
    selected_fields=list(TEMPLATE_FIELDS) if selected_fields is None else selected_fields
    if not isinstance(selected_fields,list) or not selected_fields or any(key not in TEMPLATE_FIELDS for key in selected_fields):
        raise ValueError('请选择有效的角色卡更新字段')
    snapshot=copy.deepcopy(state.template_snapshot)
    snapshot.update({key:copy.deepcopy(getattr(character,key)) for key in selected_fields})
    values={'template_snapshot':snapshot}
    if reset_state:
        values.update({key:copy.deepcopy(getattr(character,key)) for key in STATE_FIELDS if key in selected_fields})
    _save_state(state,values,expected_revision)
    Conversation.objects.filter(pk=conversation.pk).update(story_revision=F('story_revision')+1)
    conversation.refresh_from_db()
    return state
