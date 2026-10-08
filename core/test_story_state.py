import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .generation import apply_result, build_messages
from .models import Character, Conversation, ConversationParticipant, Message, UserProfile
from .settings_api import effective_values


class StoryStateTests(TestCase):
    def test_expired_or_replaced_job_lease_cannot_write_messages_or_state(self):
        from datetime import timedelta
        from .models import GenerationJob
        from .story_state import ensure_story_state,StoryConflict
        state=ensure_story_state(self.a,self.actor)
        job=GenerationJob.objects.create(conversation=self.a,requested_by=self.user,actor_ids=[str(self.actor.pk)],status='running',lease_token='新令牌',lease_until=timezone.now()+timedelta(minutes=2))
        for token,expiry in [('旧令牌',timezone.now()+timedelta(minutes=2)),('新令牌',timezone.now()-timedelta(seconds=1))]:
            job.lease_until=expiry;job.save()
            with self.assertRaises(StoryConflict):
                apply_result(self.a,self.actor,{'dialogue':'过期结果','state_updates':{'情绪':'过期'}},[],expected_revision=state.revision,job_id=job.pk,lease_token=token)
        self.assertFalse(self.a.messages.exists())
        state.refresh_from_db();self.assertEqual(state.state_fields,{'情绪':'平静'})

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='状态测试')
        UserProfile.objects.update_or_create(user=self.user, defaults={
            'policy_consent_at': timezone.now(), 'privacy_policy_version': PRIVACY_POLICY_VERSION,
            'usage_rules_version': USAGE_RULES_VERSION,
        })
        self.actor = Character.objects.create(owner=self.user, name='NPC', state_fields={'情绪':'平静'}, affinity=10)
        self.player = Character.objects.create(owner=self.user, name='玩家', is_player_controlled=True)
        self.a = self.story('甲')
        self.b = self.story('乙')
        self.client.force_login(self.user)

    def story(self, title):
        c = Conversation.objects.create(owner=self.user, title=title, player_character=self.player)
        ConversationParticipant.objects.create(conversation=c, character=self.player, position=0)
        ConversationParticipant.objects.create(conversation=c, character=self.actor, position=1)
        return c

    def test_generated_state_does_not_change_template_or_other_story(self):
        apply_result(self.a, self.actor, {'state_updates':{'情绪':'愉快'}, 'fixed_status':{'affinity':20}, 'dialogue':'你好'}, [])
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.state_fields, {'情绪':'平静'})
        self.assertEqual(self.actor.affinity, 10)
        a_context = json.loads(build_messages(self.a, self.actor, '', effective_values(self.user))[0]['content'])
        b_context = json.loads(build_messages(self.b, self.actor, '', effective_values(self.user))[0]['content'])
        self.assertEqual(a_context['character']['state_fields']['情绪'], '愉快')
        self.assertEqual(b_context['character']['state_fields']['情绪'], '平静')

    def test_state_api_rejects_stale_revision_and_foreign_user(self):
        url = f'/api/conversations/{self.a.pk}/states/{self.actor.pk}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        revision = response.json()['revision']
        data = {'revision':revision, 'state_fields':{'情绪':'紧张'}}
        self.assertEqual(self.client.patch(url, json.dumps(data), content_type='application/json').status_code, 200)
        self.assertEqual(self.client.patch(url, json.dumps(data), content_type='application/json').status_code, 409)
        other = get_user_model().objects.create_user(username='另一账号')
        UserProfile.objects.update_or_create(user=other, defaults={
            'policy_consent_at': timezone.now(), 'privacy_policy_version': PRIVACY_POLICY_VERSION,
            'usage_rules_version': USAGE_RULES_VERSION,
        })
        self.client.force_login(other)
        self.assertIn(self.client.get(url).status_code, [403,404])

    def test_previous_message_snapshot_is_recovered(self):
        from .story_state import ensure_story_state
        Message.objects.create(conversation=self.a, speaker=self.actor, kind=Message.STATE,
                               state_snapshot={'情绪':'疲惫','affinity':35})
        state = ensure_story_state(self.a, self.actor)
        self.assertEqual(state.state_fields, {'情绪':'疲惫'})
        self.assertEqual(state.affinity, 35)
        self.assertEqual(state.initialization_source, '历史状态记录')

    def test_generation_revision_conflict_prevents_messages(self):
        from .story_state import ensure_story_state, update_story_state, StoryConflict
        state = ensure_story_state(self.a, self.actor)
        old = state.revision
        update_story_state(self.a, self.actor, {'state_fields':{'情绪':'用户编辑'}}, old)
        before = self.a.messages.count()
        with self.assertRaises(StoryConflict):
            apply_result(self.a, self.actor, {'state_updates':{'情绪':'过期'},'dialogue':'过期回复'}, [], expected_revision=old)
        self.assertEqual(self.a.messages.count(), before)

    def test_non_participant_cannot_have_state(self):
        from .story_state import ensure_story_state
        stranger = Character.objects.create(owner=self.user, name='未在场角色')
        with self.assertRaises(ValueError):
            ensure_story_state(self.a, stranger)

    def test_template_update_can_apply_selected_fields(self):
        from .story_state import ensure_story_state, apply_template_update
        state=ensure_story_state(self.a,self.actor)
        self.actor.personality='新的完整设定'
        self.actor.memories='新的背景'
        self.actor.save()
        updated=apply_template_update(self.a,self.actor,state.revision,selected_fields=['personality'])
        self.assertEqual(updated.template_snapshot['personality'],'新的完整设定')
        self.assertEqual(updated.template_snapshot['memories'],'')

    def test_migration_preserves_accounts_and_recovers_snapshots(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from .models import ConversationActorState
        Message.objects.create(conversation=self.a,speaker=self.actor,kind=Message.STATE,
                               state_snapshot={'情绪':'迁移前状态','affinity':44})
        before=get_user_model().objects.count()
        migration=importlib.import_module('core.migrations.0018_initialize_story_states')
        from types import SimpleNamespace
        migration.initialize_states(apps,SimpleNamespace(connection=connection))
        self.assertEqual(get_user_model().objects.count(),before)
        state=ConversationActorState.objects.get(conversation=self.a,character=self.actor)
        self.assertEqual(state.affinity,44)
        self.assertEqual(state.state_fields['情绪'],'迁移前状态')
        fallback=ConversationActorState.objects.get(conversation=self.b,character=self.actor)
        self.assertEqual(fallback.initialization_source,'升级时角色卡')
