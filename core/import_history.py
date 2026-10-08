import hashlib
import json
from django.db import transaction,OperationalError
from django.forms.models import model_to_dict
from .models import ImportBatch,ImportTask,Character,CharacterCategory,Worldbook,WorldbookCategory,StoryCheckpoint,Message,Conversation
from .bundle_transfer import parse_bundle,commit_bundle

MODELS={'character':Character,'worldbook':Worldbook,'character_category':CharacterCategory,'worldbook_category':WorldbookCategory}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def owned(model,owner):
    return model.objects.filter(**({'worldbook__owner':owner} if model==WorldbookCategory else {'owner':owner}))


def fingerprint(obj):
    data=model_to_dict(obj)
    if isinstance(obj,Worldbook):
        data['entries']=[{**model_to_dict(row),'categories':list(row.categories.values_list('id',flat=True)),'scoped_characters':list(row.scoped_characters.values_list('id',flat=True)),'scoped_conversations':list(row.scoped_conversations.values_list('id',flat=True))} for row in obj.entries.order_by('id')]
        data['categories']=list(obj.categories.order_by('id').values())
    return digest(data)


def commit_import_batch(owner,payload,idempotency_key,task_id=None,*,operation=None):
    import time
    for attempt in range(40):
        try:
            return _commit_import_batch(owner,payload,idempotency_key,task_id,operation=operation)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() or attempt==39:
                raise
            time.sleep(.05)


def _commit_import_batch(owner,payload,idempotency_key,task_id=None,*,operation=None):
    if not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=200:
        raise ValueError('导入提交标识无效')
    hashed=digest(payload)
    with transaction.atomic():
        batch,created=ImportBatch.objects.get_or_create(owner=owner,idempotency_key=idempotency_key,defaults={'payload_hash':hashed})
        if not created:
            if batch.payload_hash!=hashed:
                raise ValueError('同一提交标识对应的内容已变化，请重新检查')
            return batch.result
        if task_id:
            task=ImportTask.objects.filter(pk=task_id,owner=owner).first()
            if not task:
                raise ValueError('来源任务不存在或不属于当前账号')
            batch.task=task
        before={name:set(owned(model,owner).values_list('pk',flat=True)) for name,model in MODELS.items()}
        result=operation() if operation is not None else commit_bundle(parse_bundle(payload,owner),owner)
        created_objects=[]
        for name,model in MODELS.items():
            for obj in owned(model,owner).exclude(pk__in=before[name]):
                created_objects.append({'kind':name,'id':str(obj.pk),'name':obj.name,'fingerprint':fingerprint(obj)})
        batch.created_objects=created_objects
        batch.result={**result,'batch_id':str(batch.pk)}
        batch.save(update_fields=['task','created_objects','result'])
    return batch.result


def _batch(owner,batch_id):
    batch=ImportBatch.objects.filter(owner=owner,pk=batch_id).first()
    if not batch:
        raise ValueError('导入批次不存在或不属于当前账号')
    return batch


def preview_import_undo(owner,batch_id):
    batch=_batch(owner,batch_id)
    deleted=[]
    retained=[]
    missing=[]
    snapshots='\n'.join(json.dumps(row,ensure_ascii=False,default=str) for row in StoryCheckpoint.objects.filter(conversation__owner=owner).values_list('snapshot',flat=True))
    snapshots+='\n'.join(json.dumps(row,ensure_ascii=False,default=str) for row in Message.objects.filter(conversation__owner=owner).exclude(story_snapshot={}).values_list('story_snapshot',flat=True))
    for record in batch.created_objects:
        obj=owned(MODELS[record['kind']],owner).filter(pk=record['id']).first()
        if not obj:
            missing.append(record)
            continue
        reason=''
        if fingerprint(obj)!=record['fingerprint']:
            reason='导入后已修改'
        elif record['id'] in snapshots:
            reason='已被故事存档或历史快照引用'
        elif isinstance(obj,Character) and (obj.conversations.exists() or Conversation.objects.filter(player_character=obj).exists() or obj.scoped_worldbook_entries.exclude(worldbook_id__in=[row['id'] for row in batch.created_objects if row['kind']=='worldbook']).exists()):
            reason='已用于对话'
        elif isinstance(obj,Worldbook) and (obj.enabled_in_conversations.exists() or any(config.exists() for config in (obj.entries.filter(manually_enabled_in_conversations__isnull=False),obj.entries.filter(excluded_in_conversations__isnull=False),obj.categories.filter(enabled_in_conversations__isnull=False),obj.categories.filter(excluded_in_conversations__isnull=False)))):
            reason='已被对话选用'
        elif isinstance(obj,WorldbookCategory) and str(obj.worldbook_id) not in {row['id'] for row in batch.created_objects if row['kind']=='worldbook'}:
            reason='所属世界书不是本批次新建，保留其分类及条目'
        elif isinstance(obj,CharacterCategory) and (obj.characters.exclude(pk__in=[row['id'] for row in batch.created_objects if row['kind']=='character']).exists() or obj.children.exclude(pk__in=[row['id'] for row in batch.created_objects if row['kind']=='character_category']).exists()):
            reason='分类被其他素材使用或仍有子分类'
        if reason:
            retained.append({**record,'reason':reason})
        else:
            deleted.append(record)
    # 被保留角色所属的分类与专属世界书也必须保留。
    kept_actors=Character.objects.filter(pk__in=[row['id'] for row in retained if row['kind']=='character'])
    kept_books=set(str(value) for value in kept_actors.values_list('personal_worldbook_id',flat=True) if value)
    kept_categories=set(str(value) for value in CharacterCategory.objects.filter(characters__in=kept_actors).values_list('pk',flat=True))
    for row in deleted[:]:
        if (row['kind']=='worldbook' and row['id'] in kept_books) or (row['kind']=='character_category' and row['id'] in kept_categories):
            deleted.remove(row)
            retained.append({**row,'reason':'被保留的角色仍在使用'})
    changed=True
    while changed:
        changed=False
        kept_category_ids=[row['id'] for row in retained if row['kind']=='character_category']
        kept_book_ids=[row['id'] for row in retained if row['kind']=='worldbook']
        for row in deleted[:]:
            keep=(row['kind']=='character_category' and CharacterCategory.objects.filter(parent_id=row['id'],pk__in=kept_category_ids).exists()) or (row['kind']=='worldbook_category' and WorldbookCategory.objects.filter(pk=row['id'],worldbook_id__in=kept_book_ids).exists()) or (row['kind']=='character' and Character.objects.filter(pk=row['id'],scoped_worldbook_entries__worldbook_id__in=kept_book_ids).exists())
            if keep:
                deleted.remove(row)
                retained.append({**row,'reason':'保留的子分类或世界书仍在使用'})
                changed=True
    return {'batch_id':str(batch.pk),'revision':batch.revision,'deleted':deleted,'retained':retained,'missing':missing,'already_undone':bool(batch.undo_result)}


def undo_import_batch(owner,batch_id,expected_revision):
    with transaction.atomic():
        batch=_batch(owner,batch_id)
        if batch.undo_result:
            return batch.undo_result
        changed=ImportBatch.objects.filter(pk=batch.pk,revision=expected_revision).update(revision=expected_revision+1)
        if not changed:
            raise ValueError('批次已变化，请重新预览')
        preview=preview_import_undo(owner,batch_id)
        for kind in ('character','worldbook_category','worldbook','character_category'):
            for row in preview['deleted']:
                if row['kind']==kind:
                    owned(MODELS[kind],owner).filter(pk=row['id']).delete()
        result={'batch_id':str(batch.pk),'deleted':preview['deleted'],'retained':preview['retained'],'missing':preview['missing']}
        ImportBatch.objects.filter(pk=batch.pk).update(undo_result=result)
    return result
