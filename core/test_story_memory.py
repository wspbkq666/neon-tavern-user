import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import Character, Conversation, ConversationParticipant, Message, UserProfile
from .generation import build_messages, maintain_context
from .settings_api import effective_values


class StoryMemoryTests(TestCase):
    def test_summary_processes_all_messages_in_order_across_batches(self):
        import json
        with patch('core.story_snapshots.capture_context',return_value={}):
            rows=Message.objects.bulk_create([Message(conversation=self.story,speaker=self.actor,kind='dialogue',content=f'事件{i:03}') for i in range(205)])
            from datetime import timedelta
            base=timezone.now()
            for index,row in enumerate(rows): row.created_at=base+timedelta(seconds=index)
            Message.objects.bulk_update(rows,['created_at'])
        seen=[]
        def model(messages,*args,**kwargs):
            data=json.loads(messages[1]['content']);seen.extend(data['messages'])
            return {'summary':'累计事件'}
        with patch('core.generation.call_json_model',side_effect=model) as request:
            maintain_context(self.story,effective_values(self.user),'不用真实密钥',force_memory=True)
        self.assertEqual(request.call_count,3)
        self.assertEqual(len(seen),205)
        self.assertTrue(seen[0].endswith('事件000'))
        self.assertTrue(seen[-1].endswith('事件204'))

    def test_expired_memory_job_cannot_save_summary(self):
        from datetime import timedelta
        from .models import GenerationJob
        job=GenerationJob.objects.create(conversation=self.story,requested_by=self.user,task_kind='memory',actor_ids=[],status='running',lease_token='旧租约',lease_until=timezone.now()-timedelta(seconds=1))
        Message.objects.create(conversation=self.story,speaker=self.actor,kind='dialogue',content='不能保存过期总结')
        with patch('core.generation.call_json_model',return_value={'summary':'过期总结'}):
            maintain_context(self.story,effective_values(self.user),'unused',force_memory=True,expected_story_revision=self.story.story_revision,job_id=job.pk,lease_token='旧租约')
        self.story.refresh_from_db()
        self.assertNotEqual(self.story.long_memory,'过期总结')

    def setUp(self):
        self.user=get_user_model().objects.create_user(username='记忆测试')
        UserProfile.objects.update_or_create(user=self.user,defaults={'policy_consent_at':timezone.now(),
            'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.player=Character.objects.create(owner=self.user,name='玩家',is_player_controlled=True)
        self.actor=Character.objects.create(owner=self.user,name='NPC')
        self.story=Conversation.objects.create(owner=self.user,title='记忆故事',player_character=self.player)
        ConversationParticipant.objects.create(conversation=self.story,character=self.actor,position=0)
        self.client.force_login(self.user)

    def test_manual_memory_wins_over_stale_summary(self):
        from .story_memory import update_memory,apply_memory_summary
        old=self.story.memory_revision
        update_memory(self.story,'用户修改记忆',[{'id':'事实一','text':'角色不能飞行'}],old)
        self.assertFalse(apply_memory_summary(self.story,'过期总结',old,30))
        self.story.refresh_from_db()
        self.assertEqual(self.story.long_memory,'用户修改记忆')
        self.assertEqual(self.story.locked_facts,[{'id':'事实一','text':'角色不能飞行'}])

    def test_summary_preserves_locked_fact_and_injects_it(self):
        from .story_memory import update_memory,apply_memory_summary
        update_memory(self.story,'旧记忆',[{'id':'事实一','text':'角色不能飞行'}],0)
        self.assertTrue(apply_memory_summary(self.story,'新事件',self.story.memory_revision,30))
        system=json.loads(build_messages(self.story,self.actor,'',effective_values(self.user))[0]['content'])
        self.assertEqual(system['locked_facts'],[{'id':'事实一','text':'角色不能飞行'}])

    def test_memory_api_revision_and_history(self):
        url=f'/api/conversations/{self.story.pk}/memory/'
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        data={'revision':response.json()['revision'],'text':'手动记忆','locked_facts':[{'id':'一','text':'永远保留的原文'}]}
        response=self.client.patch(url,json.dumps(data),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.patch(url,json.dumps(data),content_type='application/json').status_code,409)
        self.assertEqual(len(self.client.get(url).json()['history']),1)

    def test_auto_summary_conflict_preserves_manual_memory(self):
        from .story_memory import update_memory
        for index in range(35):
            Message.objects.create(conversation=self.story,speaker=self.actor,kind=Message.DIALOGUE,content=f'事件{index}')
        def model(*args,**kwargs):
            update_memory(self.story,'生成期间用户修改',[{'id':'一','text':'锁定'}],self.story.memory_revision)
            return {'summary':'旧模型响应'}
        with patch('core.generation.call_json_model',side_effect=model):
            maintain_context(self.story,{**effective_values(self.user),'profile_threshold':1000},'测试密钥')
        self.story.refresh_from_db()
        self.assertEqual(self.story.long_memory,'生成期间用户修改')

    def test_summary_error_is_visible(self):
        for index in range(35):
            Message.objects.create(conversation=self.story,speaker=self.actor,kind=Message.DIALOGUE,content=f'事件{index}')
        with patch('core.generation.call_json_model',side_effect=RuntimeError('测试错误')):
            maintain_context(self.story,{**effective_values(self.user),'profile_threshold':1000},'测试密钥')
        self.story.refresh_from_db()
        self.assertEqual(self.story.memory_summary_status,'failed')
        self.assertTrue(self.story.memory_summary_error)
