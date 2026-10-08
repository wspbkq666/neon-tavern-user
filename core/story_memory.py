import copy

from django.db import transaction
from django.db.models import F

from .models import Conversation, StoryMemoryRevision, GenerationJob
from django.utils import timezone
from .story_state import StoryConflict


def memory_context(conversation):
    conversation.refresh_from_db(fields=['long_memory','locked_facts','memory_revision'])
    return {'long_memory':conversation.long_memory,'locked_facts':copy.deepcopy(conversation.locked_facts)}


def validate_memory(text,locked_facts,revision):
    if type(revision) is not int or not isinstance(text,str) or len(text)>20000:
        raise ValueError('记忆文字不能超过 20,000 字，且需提供有效版本')
    if not isinstance(locked_facts,list) or len(locked_facts)>100:
        raise ValueError('最多保存 100 条锁定事实')
    seen=set()
    for fact in locked_facts:
        if not isinstance(fact,dict) or set(fact)!={'id','text'} or not isinstance(fact['id'],str) or not 1<=len(fact['id'])<=60 or fact['id'] in seen:
            raise ValueError('锁定事实编号无效或重复')
        if not isinstance(fact['text'],str) or not fact['text'].strip() or len(fact['text'])>2000:
            raise ValueError('锁定事实不能为空或超过 2,000 字')
        seen.add(fact['id'])


@transaction.atomic
def update_memory(conversation,text,locked_facts,expected_revision):
    validate_memory(text,locked_facts,expected_revision)
    changed=Conversation.objects.filter(pk=conversation.pk,memory_revision=expected_revision).update(
        long_memory=text,locked_facts=copy.deepcopy(locked_facts),memory_revision=F('memory_revision')+1,
        story_revision=F('story_revision')+1,memory_summary_status='idle',memory_summary_error='')
    if not changed:
        raise StoryConflict('记忆已变化，请重新读取后再修改')
    conversation.refresh_from_db()
    StoryMemoryRevision.objects.create(conversation=conversation,revision=conversation.memory_revision,text=text,
        locked_facts=locked_facts,source='用户修改',message_count=conversation.memory_message_count)
    return conversation


@transaction.atomic
def apply_memory_summary(conversation,summary,expected_revision,message_count,*,expected_story_revision=None,job_id=None,lease_token=None):
    if not isinstance(summary,str) or len(summary)>20000:
        raise ValueError('记忆总结格式无效或超过 20,000 字')
    query=Conversation.objects.filter(pk=conversation.pk,memory_revision=expected_revision)
    if expected_story_revision is not None:
        query=query.filter(story_revision=expected_story_revision)
    if job_id is not None:
        # 先取得任务写锁，取消和租约回收必须与本次提交串行。
        if not GenerationJob.objects.filter(pk=job_id,conversation=conversation,status='running',lease_token=lease_token,lease_until__gte=timezone.now()).update(lease_token=lease_token):
            return False
    changed=query.update(long_memory=summary,memory_revision=F('memory_revision')+1,
        memory_message_count=message_count,memory_summary_status='done',memory_summary_error='')
    if not changed:
        return False
    conversation.refresh_from_db()
    StoryMemoryRevision.objects.create(conversation=conversation,revision=conversation.memory_revision,text=summary,
        locked_facts=conversation.locked_facts,source='自动总结',message_count=message_count)
    return True


def memory_payload(conversation):
    conversation.refresh_from_db()
    return {'text':conversation.long_memory,'locked_facts':conversation.locked_facts,'revision':conversation.memory_revision,
            'status':conversation.memory_summary_status,'error':conversation.memory_summary_error,
            'message_count':conversation.memory_message_count,
            'history':[{'revision':item.revision,'source':item.source,'text':item.text,'locked_facts':item.locked_facts,
                        'message_count':item.message_count,'created_at':item.created_at.isoformat()}
                       for item in conversation.memory_history.all()[:50]]}
