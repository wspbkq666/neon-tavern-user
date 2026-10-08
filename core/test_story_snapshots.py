from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
import json
from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION

from .models import Character, Conversation, ConversationParticipant, Message, Worldbook, WorldbookEntry, ConversationWorldbookConfig, UserProfile
from .generation import apply_result
from .story_state import ensure_story_state, update_story_state, StoryConflict


class StorySnapshotTests(TestCase):
    def test_branch_preserves_personal_worldbook_and_generation_parameters(self):
        from .story_snapshots import create_checkpoint,fork_checkpoint
        from .settings_api import user_settings
        from .generation import build_messages
        from .settings_api import effective_values
        self.actor.character_worldbook={'entries':[{'name':'专属规则','content':'原始专属规则','trigger_mode':'always'}]}
        self.actor.save()
        preferences=user_settings(self.user);preferences.overrides={'temperature':0.3};preferences.save()
        checkpoint=create_checkpoint(self.story,'专属版本')
        self.actor.character_worldbook['entries'][0]['content']='后来修改的专属规则';self.actor.save()
        branch=fork_checkpoint(self.user,checkpoint,'专属分支')
        system=json.loads(build_messages(branch,self.actor,'',effective_values(self.user))[0]['content'])
        self.assertIn('原始专属规则',json.dumps(system,ensure_ascii=False))
        self.assertNotIn('后来修改的专属规则',json.dumps(system,ensure_ascii=False))
        self.assertEqual(branch.generation_configuration['temperature'],0.3)

    def setUp(self):
        self.user=get_user_model().objects.create_user(username='存档测试')
        self.player=Character.objects.create(owner=self.user,name='玩家',is_player_controlled=True)
        self.actor=Character.objects.create(owner=self.user,name='角色',state_fields={'情绪':'平静'})
        self.story=Conversation.objects.create(owner=self.user,title='原故事',player_character=self.player)
        ConversationParticipant.objects.create(conversation=self.story,character=self.player,position=0)
        ConversationParticipant.objects.create(conversation=self.story,character=self.actor,position=1)

    def test_branch_keeps_state_and_original_unchanged(self):
        from .story_snapshots import create_checkpoint,fork_checkpoint
        apply_result(self.story,self.actor,{'state_updates':{'情绪':'快乐'},'dialogue':'当前节点'},[])
        checkpoint=create_checkpoint(self.story,'快乐节点')
        branch=fork_checkpoint(self.user,checkpoint,'分支')
        original=ensure_story_state(self.story,self.actor)
        copied=ensure_story_state(branch,self.actor)
        update_story_state(branch,self.actor,{'state_fields':{'情绪':'紧张'}},copied.revision)
        original.refresh_from_db()
        self.assertEqual(original.state_fields['情绪'],'快乐')
        self.assertEqual(branch.messages.count(),self.story.messages.count())
        self.assertFalse(branch.auto_generate)

    def test_restore_removes_future_messages_and_can_undo_restore(self):
        from .story_snapshots import create_checkpoint,restore_checkpoint
        checkpoint=create_checkpoint(self.story,'最初')
        apply_result(self.story,self.actor,{'state_updates':{'情绪':'疲惫'},'dialogue':'未来消息'},[])
        result=restore_checkpoint(self.story,checkpoint,self.story.story_revision)
        self.assertFalse(self.story.messages.filter(content='未来消息').exists())
        self.assertEqual(ensure_story_state(self.story,self.actor).state_fields['情绪'],'平静')
        before_restore=self.story.checkpoints.get(pk=result['recovery_checkpoint_id'])
        self.story.refresh_from_db()
        restore_checkpoint(self.story,before_restore,self.story.story_revision)
        self.assertTrue(self.story.messages.filter(content='未来消息').exists())

    def test_checkpoint_rejects_unknown_legacy_boundary(self):
        from .story_snapshots import create_checkpoint
        message=Message.objects.create(conversation=self.story,speaker=self.actor,kind=Message.DIALOGUE,content='旧节点')
        Message.objects.filter(pk=message.pk).update(story_snapshot={})
        with self.assertRaisesRegex(ValueError,'历史状态'):
            create_checkpoint(self.story,'旧存档',str(message.pk))

    def test_worldbook_version_is_preserved_in_branch(self):
        from .story_snapshots import create_checkpoint,fork_checkpoint
        from .worldbook_resolver import resolve_worldbook_entries
        book=Worldbook.objects.create(owner=self.user,name='世界设定')
        entry=WorldbookEntry.objects.create(worldbook=book,name='规则',content='不能飞行',trigger_mode='always')
        config=ConversationWorldbookConfig.objects.create(conversation=self.story)
        config.enabled_worldbooks.add(book)
        checkpoint=create_checkpoint(self.story,'原规则')
        entry.content='已经改变的规则'
        entry.save()
        branch=fork_checkpoint(self.user,checkpoint,'旧世界分支')
        entries=resolve_worldbook_entries(branch,self.actor,'')
        self.assertEqual([item.content for item in entries],['不能飞行'])

    def test_restore_rejects_stale_story_revision(self):
        from .story_snapshots import create_checkpoint,restore_checkpoint
        checkpoint=create_checkpoint(self.story,'节点')
        state=ensure_story_state(self.story,self.actor)
        update_story_state(self.story,self.actor,{'state_fields':{'情绪':'手动修改'}},state.revision)
        with self.assertRaises(StoryConflict):
            restore_checkpoint(self.story,checkpoint,0)

    def test_checkpoint_api_preview_and_fork(self):
        UserProfile.objects.update_or_create(user=self.user,defaults={'policy_consent_at':timezone.now(),
            'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.client.force_login(self.user)
        url=f'/api/conversations/{self.story.pk}/checkpoints/'
        response=self.client.post(url,json.dumps({'name':'页面节点'}),content_type='application/json')
        self.assertEqual(response.status_code,201)
        checkpoint_id=response.json()['id']
        detail=f'{url}{checkpoint_id}/'
        preview=self.client.get(detail)
        self.assertEqual(preview.status_code,200)
        self.assertIn('changes',preview.json())
        fork=self.client.post(detail,json.dumps({'action':'fork','title':'页面分支'}),content_type='application/json')
        self.assertEqual(fork.status_code,201)
        self.assertNotEqual(fork.json()['conversation_id'],str(self.story.pk))
