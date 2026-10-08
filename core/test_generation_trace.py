from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Character,Conversation,GenerationJob
from .generation_trace import record_context_trace,public_context_trace


class GenerationTraceTests(TestCase):
    def test_actual_reply_references_frozen_trace_and_diagnostics_are_specific(self):
        from .models import ConversationParticipant,Worldbook,WorldbookEntry,ConversationWorldbookConfig
        from .generation import _generation_input,apply_result
        from .settings_api import effective_values
        user=get_user_model().objects.create_user(username='回复来源测试')
        actor=Character.objects.create(owner=user,name='NPC')
        player=Character.objects.create(owner=user,name='玩家',is_player_controlled=True)
        story=Conversation.objects.create(owner=user,title='来源故事',player_character=player)
        ConversationParticipant.objects.create(conversation=story,character=actor,position=0)
        ConversationParticipant.objects.create(conversation=story,character=player,position=1)
        book=Worldbook.objects.create(owner=user,name='来源世界书')
        selected=WorldbookEntry.objects.create(worldbook=book,name='常驻规则',content='旧规则',trigger_mode='always')
        disabled=WorldbookEntry.objects.create(worldbook=book,name='关闭条目',content='不应采用',enabled=False,trigger_mode='always')
        unmatched=WorldbookEntry.objects.create(worldbook=book,name='雨天',content='仅雨天',keywords=['雨'])
        config=ConversationWorldbookConfig.objects.create(conversation=story);config.enabled_worldbooks.add(book)
        job=GenerationJob.objects.create(conversation=story,requested_by=user,actor_ids=[str(actor.pk)])
        _,_,metadata=_generation_input(story,actor,None,effective_values(user),job=job)
        trace=job.context_traces.get()
        reasons={row['id']:row['reason'] for row in trace.excluded}
        self.assertEqual(reasons[str(disabled.pk)],'条目已禁用')
        self.assertEqual(reasons[str(unmatched.pk)],'关键词未出现在近期公开消息中')
        apply_result(story,actor,{'dialogue':'测试回复'},[],context_trace_id=metadata['trace_id'])
        self.assertEqual(str(story.messages.get(kind='dialogue').context_trace_id),metadata['trace_id'])
        selected.content='新规则';selected.save()
        self.assertEqual(public_context_trace(trace)['selected'][0]['content'],'旧规则')
        from .story_snapshots import create_checkpoint,fork_checkpoint
        checkpoint=create_checkpoint(story,'回复记录存档')
        branch=fork_checkpoint(user,checkpoint,'回复记录分支')
        restored=branch.messages.get(kind='dialogue').context_trace
        self.assertEqual(restored.job.conversation,branch)
        self.assertEqual(restored.selected[0]['content'],'旧规则')

    def test_trace_freezes_content_and_whitelists_configuration(self):
        user=get_user_model().objects.create_user(username='诊断用户')
        actor=Character.objects.create(owner=user,name='NPC')
        player=Character.objects.create(owner=user,name='玩家',is_player_controlled=True)
        story=Conversation.objects.create(owner=user,title='诊断',player_character=player)
        job=GenerationJob.objects.create(conversation=story,requested_by=user,actor_ids=[str(actor.pk)])
        trace=record_context_trace(job,actor,[{'id':'条目编号','content':'当时设定','name':'当时条目'}],[{'id':'未选','reason':'预算不足'}],{'estimated':True},options={'model':'测试','api_key':'秘密','global_prompt':'隐藏管理提示词'})
        public=public_context_trace(trace)
        self.assertEqual(public['selected'][0]['content'],'当时设定')
        self.assertNotIn('api_key',public['configuration'])
        self.assertNotIn('global_prompt',public['configuration'])
        self.assertEqual(public['excluded'][0]['reason'],'预算不足')
