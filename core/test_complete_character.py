import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import Character, UserProfile
from .character_api import _native_character
from .bundle_transfer import export_bundle,parse_bundle,commit_bundle


class CompleteCharacterTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='完整字段')
        UserProfile.objects.update_or_create(user=self.user,defaults={'policy_consent_at':timezone.now(),
            'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.client.force_login(self.user)
        self.fields={'name':'测试角色','scenario':'雨夜的酒馆','first_mes':'你终于来了。',
            'alternate_greetings':['好久不见。'],'character_worldbook':{'entries':[{'name':'规则','content':'不能飞行','trigger_mode':'always'}]}}

    def test_native_fields_are_not_dropped(self):
        data=_native_character(self.fields)
        for key in ('scenario','first_mes','alternate_greetings','character_worldbook'):
            self.assertEqual(data.get(key),self.fields[key])

    def test_create_export_and_bundle_roundtrip(self):
        response=self.client.post('/api/characters/',json.dumps(self.fields),content_type='application/json')
        self.assertEqual(response.status_code,201)
        actor=Character.objects.get(pk=response.json()['id'])
        self.assertEqual(actor.personal_worldbook.entries.get().content,'不能飞行')
        target=get_user_model().objects.create_user(username='导入目标')
        payload=export_bundle(self.user,[str(actor.pk)],[])
        result=commit_bundle(parse_bundle(payload,target),target)
        copied=Character.objects.get(pk=result['characters'][0]['id'])
        self.assertEqual(copied.first_mes,actor.first_mes)
        self.assertEqual(copied.alternate_greetings,actor.alternate_greetings)
        self.assertEqual(copied.scenario,actor.scenario)
        self.assertEqual(copied.personal_worldbook.entries.get().content,'不能飞行')

    def test_invalid_greeting_array_is_rejected_without_creating_character(self):
        response=self.client.post('/api/characters/',json.dumps({**self.fields,'alternate_greetings':[12]}),content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertFalse(Character.objects.filter(owner=self.user).exists())

    def test_editing_personal_lore_keeps_entry_id_and_priority(self):
        actor=Character.objects.create(owner=self.user,**self.fields)
        entry=actor.personal_worldbook.entries.get()
        actor.character_worldbook['entries'][0].update(content='不能凭空飞行',priority=42)
        actor.save()
        updated=actor.personal_worldbook.entries.get()
        self.assertEqual(updated.pk,entry.pk)
        self.assertEqual(updated.priority,42)
        self.assertEqual(updated.content,'不能凭空飞行')

    def test_partial_save_does_not_write_unsaved_lore(self):
        actor=Character.objects.create(owner=self.user,**self.fields)
        actor.character_worldbook={'entries':[{'name':'另一个','content':'尚未保存','trigger_mode':'always'}]}
        actor.summary='更新简介'
        actor.save(update_fields=['summary'])
        self.assertEqual(actor.personal_worldbook.entries.get().content,'不能飞行')

    def test_smart_import_keeps_supported_fields(self):
        from .smart_import import normalize_smart_import
        drafts,payload=normalize_smart_import({'items':[{'type':'npc','fields':self.fields,'source_excerpt':'角色原文','confidence':1,'warnings':[]}]})
        for key in ('first_mes','alternate_greetings','character_worldbook'):
            self.assertEqual(payload['characters'][0].get(key),self.fields[key])
            self.assertNotIn(key,drafts[0].get('unmapped_fields',{}))

    def test_opening_greeting_is_explicit_and_does_not_make_player_speak(self):
        actor=Character.objects.create(owner=self.user,**self.fields)
        player=Character.objects.create(owner=self.user,name='玩家',is_player_controlled=True,first_mes='不能自动发言')
        payload={'title':'选择开场白','player_character_id':str(player.pk),'character_ids':[str(actor.pk)],
                 'opening_greetings':{str(actor.pk):0}}
        response=self.client.post('/api/conversations/',json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,201)
        from .models import Message
        messages=Message.objects.filter(conversation_id=response.json()['id'])
        self.assertEqual(list(messages.values_list('content',flat=True)),['好久不见。'])
        self.assertFalse(messages.filter(speaker=player).exists())

    def test_personal_lore_applies_trigger_modes_in_generation(self):
        from .models import Conversation,ConversationParticipant
        from .generation import build_messages
        from .settings_api import effective_values
        actor=Character.objects.create(owner=self.user,**{**self.fields,'character_worldbook':{'entries':[
            {'name':'常驻','content':'不能飞行','trigger_mode':'always'},
            {'name':'雨天','content':'雨天设定','trigger_mode':'keyword','keywords':['雨']}]}})
        player=Character.objects.create(owner=self.user,name='玩家',is_player_controlled=True)
        story=Conversation.objects.create(owner=self.user,title='触发规则',player_character=player)
        ConversationParticipant.objects.create(conversation=story,character=actor,position=0)
        system=json.loads(build_messages(story,actor,'',effective_values(self.user))[0]['content'])
        content=json.dumps(system,ensure_ascii=False)
        self.assertIn('不能飞行',content)
        self.assertNotIn('雨天设定',content)

    def test_personal_lore_respects_disable_exclusion_and_management_edits(self):
        from .models import Conversation,ConversationParticipant,ConversationWorldbookConfig
        from .story_state import actor_context
        from .character_lore import resolve_personal_lore
        actor=Character.objects.create(owner=self.user,**self.fields)
        player=Character.objects.create(owner=self.user,name='禁用测试玩家',is_player_controlled=True)
        story=Conversation.objects.create(owner=self.user,title='专属禁用',player_character=player)
        ConversationParticipant.objects.create(conversation=story,character=actor,position=0)
        frozen=actor_context(story,actor)
        entry=actor.personal_worldbook.entries.get()
        entry.enabled=False;entry.save()
        self.assertEqual(resolve_personal_lore(story,frozen,''),[])
        entry.enabled=True;entry.save()
        config=ConversationWorldbookConfig.objects.create(conversation=story)
        config.excluded_entries.add(entry)
        self.assertEqual(resolve_personal_lore(story,frozen,''),[])
        config.excluded_entries.clear()
        response=self.client.patch(f'/api/worldbooks/{actor.personal_worldbook_id}/entries/{entry.pk}/',json.dumps({'content':'禁止凭空飞行'}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        actor.refresh_from_db()
        self.assertEqual(actor.character_worldbook['entries'][0]['content'],'禁止凭空飞行')
        self.assertEqual(resolve_personal_lore(story,frozen,'')[0].content,'不能飞行')
        actor.personal_worldbook.enabled=False;actor.personal_worldbook.save()
        actor=actor_context(story,actor)
        self.assertEqual(resolve_personal_lore(story,actor,''),[])
