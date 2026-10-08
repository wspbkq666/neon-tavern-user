import hashlib
import json
from .models import GenerationContextTrace

CONFIGURATION_FIELDS={'model','provider_id','temperature','top_p','max_tokens','context_capacity'}


def record_context_trace(job,actor,selected,excluded,estimated_usage,*,options=None,character_snapshot=None):
    selected=[{key:item[key] for key in ('id','name','content','position','mode','keywords','matched_keywords','scope','priority','reason','start','end','field','text','version') if key in item} for item in selected]
    excluded=[{key:item[key] for key in ('id','name','reason') if key in item} for item in excluded]
    configuration={key:value for key,value in (options or {}).items() if key in CONFIGURATION_FIELDS}
    snapshot=character_snapshot or {}
    digest=hashlib.sha256(json.dumps({'selected':selected,'character':snapshot},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return GenerationContextTrace.objects.create(job=job,actor=actor,selected=selected,excluded=excluded,estimated_usage=estimated_usage,
        configuration=configuration,character_snapshot=snapshot,content_hash=digest)


def public_context_trace(trace):
    return {'id':str(trace.pk),'actor_id':str(trace.actor_id) if trace.actor_id else None,'job_id':str(trace.job_id),'status':trace.job.status,
        'message_ids':[str(value) for value in trace.messages.values_list('pk',flat=True)],
        'selected':trace.selected,'excluded':trace.excluded,'estimated_usage':trace.estimated_usage,
        'configuration':{key:value for key,value in trace.configuration.items() if key in CONFIGURATION_FIELDS},
        'character_snapshot':trace.character_snapshot,'content_hash':trace.content_hash,'created_at':trace.created_at.isoformat()}
