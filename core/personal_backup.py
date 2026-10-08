"""当前账号的素材备份。只接受白名单模型、包内引用和验证过的图片。"""
import copy
import hashlib
import io
import json
import stat
import uuid
import zipfile
from collections import Counter
from pathlib import PurePosixPath
from django.core.files.base import ContentFile
from django.db import transaction,models
from django.db.models import Max
from PIL import Image
from . import models as data_models
from .story_snapshots import remap_boundary,suspend_capture
from django.core.exceptions import ValidationError
from .settings_api import DEFAULT_VALUES,validate_value

LIMIT=100*1024*1024
PREFERENCE_FIELDS=set(DEFAULT_VALUES)-{'api_base_url','provider_id','model'}
FIELDS={
 'CharacterCategory':'name parent position characters',
 'Worldbook':'name description enabled created_at updated_at',
 'WorldbookCategory':'worldbook name parent position',
 'Character':'name summary personality speech_habits memories scenario first_mes alternate_greetings character_worldbook context_policy personal_worldbook lore_hash relationship_notes state_fields affinity clothing_type clothing_state is_player_controlled categories created_at updated_at',
 'Conversation':'title player_character environment language auto_generate auto_actor_ids auto_mode generation_configuration long_memory locked_facts memory_revision memory_summary_status memory_summary_error memory_message_count profile_message_count story_revision created_at updated_at',
 'ConversationParticipant':'conversation character position',
 'ConversationActorState':'conversation character template_snapshot state_fields relationship_notes affinity clothing_type clothing_state revision initialization_source updated_at',
 'WorldbookEntry':'worldbook name content enabled trigger_mode keywords insertion_position priority scope_type import_metadata categories scoped_characters scoped_conversations created_at updated_at',
 'ConversationWorldbookConfig':'conversation enabled_worldbooks enabled_categories excluded_categories excluded_entries manual_entries',
 'StoryMemoryRevision':'conversation revision text locked_facts source message_count created_at',
 'GenerationJob':'conversation actor_ids director_hints mode auto status progress error story_revision task_kind created_at started_at finished_at',
 'GenerationContextTrace':'job actor selected excluded estimated_usage configuration character_snapshot content_hash created_at',
 'Message':'conversation speaker context_trace kind content source consumed_by state_snapshot story_snapshot created_at',
 'StoryCheckpoint':'conversation name snapshot created_at',
 'ImportTask':'parent filename source source_hash options status revision result coverage_reviews source_start source_end error created_at updated_at',
 'ImportSegment':'task index start end status worker_id attempts raw_output result error',
 'ImportTaskEvent':'task payload created_at',
 'ImportBatch':'task idempotency_key payload_hash created_objects result undo_result revision created_at',
}


def queryset(name,owner):
    model=getattr(data_models,name)
    if name in ('Character','CharacterCategory','Worldbook','Conversation','ImportTask','ImportBatch'):
        return model.objects.filter(owner=owner)
    if name in ('WorldbookCategory','WorldbookEntry'):
        return model.objects.filter(worldbook__owner=owner)
    if name in ('ImportSegment','ImportTaskEvent'):
        return model.objects.filter(task__owner=owner)
    if name=='GenerationContextTrace':
        return model.objects.filter(job__requested_by=owner,job__conversation__owner=owner)
    return model.objects.filter(conversation__owner=owner)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def json_refs(name,fields,mapping):
    value=copy.deepcopy(fields)
    def mapped(item):
        return mapping.get(str(item),item)
    for key in ('auto_actor_ids','actor_ids','consumed_by'):
        if key in value:
            value[key]=[mapped(item) for item in value[key]]
    if name=='ConversationActorState':
        template=value.get('template_snapshot',{})
        if template.get('_personal_worldbook_id'):
            template['_personal_worldbook_id']=mapped(template['_personal_worldbook_id'])
        from .character_lore import lore_entries
        for entry in lore_entries(template.get('character_worldbook',{})):
            if entry.get('_entry_id'):
                entry['_entry_id']=mapped(entry['_entry_id'])
    if name=='GenerationJob':
        value['director_hints']={mapped(key):item for key,item in value.get('director_hints',{}).items()}
        value['progress']={mapped(key):item for key,item in value.get('progress',{}).items()}
        for progress in value['progress'].values():
            if not isinstance(progress,dict):
                continue
            if progress.get('trace_id'):
                progress['trace_id']=mapped(progress['trace_id'])
            for key in ('worldbook_entry_ids','worldbook_omitted_entry_ids'):
                if key in progress:
                    progress[key]=[mapped(item) for item in progress[key]]
    if name=='Message' and value.get('story_snapshot'):
        value['story_snapshot']=remap_boundary(value['story_snapshot'],mapping)
    if name=='StoryCheckpoint' and value.get('snapshot'):
        snapshot=remap_boundary(value['snapshot'],mapping)
        value['snapshot']=snapshot
        for message in snapshot.get('messages',[]):
            for key in ('id','speaker_id','context_trace_id'):
                if message.get(key):
                    message[key]=mapped(message[key])
            message['consumed_by']=[mapped(item) for item in message.get('consumed_by',[])]
            if message.get('story_snapshot'):
                message['story_snapshot']=remap_boundary(message['story_snapshot'],mapping)
        for trace in snapshot.get('source_traces',[]):
            for key in ('id','actor_id','job_id'):
                if trace.get(key): trace[key]=mapped(trace[key])
            for row in [*trace.get('selected',[]),*trace.get('excluded',[])]:
                if row.get('id'): row['id']=mapped(row['id'])
    if name=='GenerationContextTrace':
        for key in ('selected','excluded'):
            for row in value.get(key,[]):
                if row.get('id'):
                    row['id']=mapped(row['id'])
    # 批次恢复作为归档历史；不沿用原账号的撤销权限和对象指纹。
    if name=='ImportBatch':
        for row in value.get('created_objects',[]):
            row['id']=mapped(row['id'])
        for key in ('result','undo_result'):
            result=value.get(key,{})
            if result.get('batch_id'):
                result['batch_id']=mapped(result['batch_id'])
            for group in ('characters','worldbooks','deleted','retained','missing'):
                for row in result.get(group,[]):
                    if row.get('id'):
                        row['id']=mapped(row['id'])
    return value


@transaction.atomic
def serialize_personal_backup(owner):
    collections={name:list(queryset(name,owner).order_by('pk')) for name in FIELDS}
    mapping={}
    by_model={}
    for name,rows in collections.items():
        by_model[name]={str(row.pk):f'{name}:{index+1}' for index,row in enumerate(rows)}
        if isinstance(getattr(data_models,name)._meta.pk,models.UUIDField):
            mapping.update(by_model[name])
    objects=[]
    assets={}
    for name,rows in collections.items():
        for row in rows:
            fields={}
            for key in FIELDS[name].split():
                field=row._meta.get_field(key)
                if field.many_to_many:
                    fields[key]=[by_model[field.related_model.__name__][str(pk)] for pk in getattr(row,key).values_list('pk',flat=True)]
                elif field.is_relation:
                    identifier=getattr(row,field.attname)
                    fields[key]=by_model[field.related_model.__name__].get(str(identifier)) if identifier is not None else None
                    if identifier is not None and fields[key] is None:
                        raise ValueError('资料中存在当前账号外或缺失的引用，无法生成完整个人备份')
                else:
                    fields[key]=getattr(row,key)
            item={'kind':name,'id':by_model[name][str(row.pk)],'fields':json_refs(name,fields,mapping)}
            if name=='Character' and row.avatar:
                with row.avatar.open('rb') as source:
                    raw=source.read(LIMIT+1)
                _verify_image(raw)
                member=f'assets/{uuid.uuid4().hex}.image'
                assets[member]=raw
                item['avatar']={'path':member,'sha256':sha(raw)}
            objects.append(item)
    settings=data_models.UserSettings.objects.filter(user=owner).first()
    preferences={key:value for key,value in (settings.overrides if settings else {}).items() if key in PREFERENCE_FIELDS and validate_value(key,value)}
    manifest={'format':'neon-tavern-personal-backup','version':1,'objects':objects,'preferences':preferences,'assets':{key:sha(raw) for key,raw in assets.items()}}
    target=io.BytesIO()
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,default=str))
        for member,raw in assets.items():
            archive.writestr(member,raw)
    package=target.getvalue()
    if len(package)>LIMIT:
        raise ValueError('个人备份超过 100 MiB，请先分开导出素材或减少历史附件')
    return package


def _verify_image(raw):
    if len(raw)>LIMIT:
        raise ValueError('图片附件过大')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG','JPEG','WEBP') or image.width*image.height>20000000:
                raise ValueError('图片类型或像素大小不支持')
            image.verify()
    except (OSError,Image.DecompressionBombError) as exc:
        raise ValueError('图片附件无效') from exc


def validate_personal_backup(owner,archive):
    raw=bytes(archive)
    if len(raw)>LIMIT:
        raise ValueError('备份文件不能超过 100 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            members=package.infolist()
            if len(members)>10000 or sum(row.file_size for row in members)>LIMIT:
                raise ValueError('备份解压总量或文件数量超过上限')
            names=set()
            for row in members:
                path=PurePosixPath(row.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in row.filename or ':' in row.filename or stat.S_ISLNK(row.external_attr>>16) or row.filename in names:
                    raise ValueError('备份包含不安全的路径或重复成员')
                if row.file_size>max(1,row.compress_size)*100:
                    raise ValueError('备份压缩比超过上限')
                names.add(row.filename)
            manifest=json.loads(package.read('manifest.json'))
            if not isinstance(manifest,dict) or set(manifest)-{'format','version','objects','preferences','assets'} or manifest.get('format')!='neon-tavern-personal-backup' or manifest.get('version')!=1 or not isinstance(manifest.get('objects'),list):
                raise ValueError('个人备份清单格式无效')
            objects=manifest['objects']
            preferences=manifest.get('preferences',{})
            if not isinstance(preferences,dict) or set(preferences)-PREFERENCE_FIELDS or any(not validate_value(key,value) for key,value in preferences.items()):
                raise ValueError('备份个人偏好配置无效或包含连接配置')
            if len(objects)>200000:
                raise ValueError('个人备份对象数量过多')
            identifiers={}
            for item in objects:
                if not isinstance(item,dict) or set(item)-{'id','kind','fields','avatar'} or item.get('kind') not in FIELDS or not isinstance(item.get('id'),str) or item['id'] in identifiers or not isinstance(item.get('fields'),dict):
                    raise ValueError('备份对象类型或编号无效')
                if set(item['fields'])-set(FIELDS[item['kind']].split()):
                    raise ValueError('备份包含不允许恢复的字段')
                identifiers[item['id']]=item['kind']
            for item in objects:
                model=getattr(data_models,item['kind'])
                scalar={}
                for key,value in item['fields'].items():
                    field=model._meta.get_field(key)
                    if field.is_relation:
                        if value is None and not field.null and not field.many_to_many:
                            raise ValueError('备份缺少必要引用')
                        refs=value if field.many_to_many else [value] if value is not None else []
                        if not isinstance(refs,list) or any(identifiers.get(ref)!=field.related_model.__name__ for ref in refs):
                            raise ValueError('备份引用不完整或引用了包外对象')
                    else:
                        converted=field.to_python(value)
                        if isinstance(field,models.JSONField) and converted in ({},[]):
                            field.run_validators(converted)
                        else:
                            field.clean(converted,model())
                        scalar[key]=converted
                for field in model._meta.fields:
                    if field.is_relation and not field.null and field.name not in ('owner','requested_by') and field.name not in item['fields']:
                        raise ValueError('备份缺少必要引用字段')
                if item['kind']=='Character':
                    from .character_lore import validate_extended_fields
                    validate_extended_fields(model(**scalar))
                    if any(len(scalar.get(key,''))>100000 for key in ('personality','memories','speech_habits')):
                        raise ValueError('备份角色设定字段超过 100,000 字')
                if item['kind']=='ImportTask':
                    from .import_tasks import OPTION_FIELDS
                    options=scalar.get('options',{})
                    if not isinstance(options,dict) or set(options)-OPTION_FIELDS or any(not validate_value(key,value) for key,value in options.items()):
                        raise ValueError('备份导入配置无效或包含私有字段')
                if item['kind']=='Conversation':
                    from .story_snapshots import GENERATION_FIELDS
                    options=scalar.get('generation_configuration',{})
                    if not isinstance(options,dict) or set(options)-set(GENERATION_FIELDS) or any(not validate_value(key,value) for key,value in options.items()):
                        raise ValueError('备份故事生成参数无效')
                if item['kind']=='GenerationContextTrace':
                    from .generation_trace import CONFIGURATION_FIELDS
                    if not isinstance(scalar.get('configuration',{}),dict) or set(scalar.get('configuration',{}))-CONFIGURATION_FIELDS:
                        raise ValueError('备份设定记录包含不允许的配置字段')
                avatar=item.get('avatar')
                if avatar and (item['kind']!='Character' or not isinstance(avatar,dict) or not avatar.get('path','').startswith('assets/')):
                    raise ValueError('头像附件引用无效')
            assets={}
            declared=manifest.get('assets',{})
            if not isinstance(declared,dict) or names!=set(declared)|{'manifest.json'}:
                raise ValueError('备份存在未声明的文件')
            for path,digest in declared.items():
                content=package.read(path)
                if sha(content)!=digest:
                    raise ValueError('附件摘要不匹配')
                _verify_image(content)
                assets[path]=content
            for item in objects:
                avatar=item.get('avatar')
                if avatar and (avatar.get('path') not in assets or sha(assets[avatar['path']])!=avatar.get('sha256')):
                    raise ValueError('头像附件缺失或摘要无效')
    except (zipfile.BadZipFile,KeyError,TypeError,UnicodeDecodeError,RecursionError,json.JSONDecodeError,ValidationError) as exc:
        raise ValueError('备份 ZIP 或清单无效') from exc
    counts=dict(Counter(item['kind'] for item in objects))
    return {'objects':objects,'assets':assets,'counts':counts,'preferences':preferences,'package_hash':sha(raw),'owner_id':owner.pk,'warnings':['恢复新增资料，不覆盖现有素材；个人偏好按备份恢复，连接配置与密钥保持现状；进行中的任务恢复为失败状态；导入历史恢复为归档，不沿用原撤销权限。']}


def restore_personal_backup(owner,preview,idempotency_key):
    if preview.get('owner_id')!=owner.pk or not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=200:
        raise ValueError('恢复请求不属于当前账号')
    saved_files=[]
    try:
        with transaction.atomic(),suspend_capture():
            receipt,created=data_models.PersonalRestoreReceipt.objects.get_or_create(owner=owner,idempotency_key=idempotency_key,defaults={'package_hash':preview['package_hash']})
            if not created:
                if receipt.package_hash!=preview['package_hash']:
                    raise ValueError('同一恢复标识对应了不同文件')
                return receipt.result
            mapping={}
            for name in FIELDS:
                model=getattr(data_models,name)
                numeric=(model.objects.aggregate(value=Max('pk'))['value'] or 0) if not isinstance(model._meta.pk,models.UUIDField) else None
                for item in preview['objects']:
                    if item['kind']==name:
                        if numeric is None:
                            mapping[item['id']]=str(uuid.uuid4())
                        else:
                            numeric+=1
                            mapping[item['id']]=numeric
            saved={}
            deferred=[]
            for name in FIELDS:
                model=getattr(data_models,name)
                rows=[]
                for item in preview['objects']:
                    if item['kind']!=name:
                        continue
                    values=json_refs(name,item['fields'],mapping)
                    fields={'pk':mapping[item['id']]}
                    for key,value in values.items():
                        field=model._meta.get_field(key)
                        if field.many_to_many:
                            deferred.append((item['id'],key,[mapping[ref] for ref in value]))
                        elif field.is_relation:
                            fields[field.attname]=mapping[value] if value is not None else None
                        else:
                            fields[key]=field.to_python(value)
                    if any(field.name=='owner' for field in model._meta.fields):
                        fields['owner_id']=owner.pk
                    if name=='GenerationJob':
                        fields['requested_by_id']=owner.pk
                    if name in ('GenerationJob','ImportTask','ImportSegment') and fields.get('status') in ('queued','running'):
                        fields.update(status='failed',error='从备份恢复的未完成任务，请手动继续或重新生成')
                    if name=='Conversation':
                        fields['auto_generate']=False
                    if name=='ImportBatch':
                        fields['idempotency_key']=f'备份归档:{uuid.uuid4().hex}'
                        fields['undo_result']={'archived':True,'retained':[],'deleted':[]}
                        fields['created_objects']=[]
                    if name in ('Character','Worldbook'):
                        original=fields['name']
                        suffix=2
                        used={row.name for row in rows}
                        maximum=60 if name=='Character' else 120
                        while model.objects.filter(owner=owner,name=fields['name']).exists() or fields['name'] in used:
                            ending=f'（恢复{suffix}）'
                            fields['name']=original[:maximum-len(ending)]+ending
                            suffix+=1
                    obj=model(**fields)
                    # 逐字段执行类型/长度校验，外键完整性已由包内引用检查负责。
                    for field in model._meta.fields:
                        if not field.is_relation and not field.primary_key:
                            current=getattr(obj,field.attname)
                            if isinstance(field,models.JSONField) and current in ({},[]):
                                field.run_validators(current)
                            else:
                                field.clean(current,obj)
                    rows.append(obj)
                    saved[item['id']]=obj
                timestamps={obj.pk:{field.name:getattr(obj,field.name) for field in model._meta.fields
                    if isinstance(field,models.DateTimeField) and (field.auto_now or field.auto_now_add) and getattr(obj,field.name) is not None} for obj in rows}
                model.objects.bulk_create(rows)
                for obj in rows:
                    if timestamps[obj.pk]:
                        model.objects.filter(pk=obj.pk).update(**timestamps[obj.pk])
                        for key,value in timestamps[obj.pk].items():
                            setattr(obj,key,value)
            for identifier,key,value in deferred:
                obj=saved[identifier]
                field=obj._meta.get_field(key)
                if field.many_to_many:
                    getattr(obj,key).set(value)
                else:
                    obj.__class__.objects.filter(pk=obj.pk).update(**{field.attname:value})
                    setattr(obj,field.attname,value)
            for item in preview['objects']:
                if item['kind']=='Character':
                    actor=saved[item['id']]
                    avatar=item.get('avatar')
                    if avatar:
                        actor.avatar.save(f'{uuid.uuid4().hex}.image',ContentFile(preview['assets'][avatar['path']]),save=False)
                        saved_files.append((actor.avatar.storage,actor.avatar.name))
                    actor.save()
            settings,_=data_models.UserSettings.objects.get_or_create(user=owner)
            settings.overrides={**settings.overrides,**preview.get('preferences',{})}
            settings.save(update_fields=['overrides','updated_at'])
            result={'counts':preview['counts'],'mapping':mapping,'warnings':preview['warnings']}
            receipt.result=result
            receipt.save(update_fields=['result'])
        return result
    except Exception:
        for storage,name in saved_files:
            storage.delete(name)
        raise
