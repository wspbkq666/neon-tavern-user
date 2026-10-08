import json
from django.contrib.auth import get_user_model
from django.test import TestCase

from .character_api import _native_character
from .models import Character
from .context_selection import split_source,select_context


class ContextSelectionTests(TestCase):
    def test_required_context_never_silently_truncates(self):
        with self.assertRaisesRegex(ValueError,'必要设定超过上下文预算'):
            select_context('不能改写的必要设定'*1000,[],[],'对话',1)

    def test_source_offsets_reproduce_every_character(self):
        text='第一段禁止飞行。\n\n第二段代号 A/B。\n'+'长段'*1500
        rows=split_source(text)
        self.assertEqual(''.join(text[row['start']:row['end']] for row in rows),text)
        self.assertTrue(all(text[row['start']:row['end']]==row['text'] for row in rows))

    def test_optional_selection_reports_reasons(self):
        result=select_context('核心规则',[],[{'id':'雨','text':'雨天背景','keywords':['雨']},{'id':'海','text':'大海背景','keywords':['海']}],'下雨了',100)
        self.assertEqual([row['id'] for row in result['selected']],['雨'])
        self.assertEqual(result['excluded'][0]['reason'],'关键词未匹配')
        self.assertTrue(result['estimated'])

    def test_native_hundred_thousand_characters_are_preserved(self):
        text='设定'*50000
        data=_native_character({'name':'长角色','personality':text,'memories':text,'speech_habits':text})
        for key in ('personality','memories','speech_habits'):
            self.assertEqual(data[key],text)

    def test_context_policy_roundtrips_in_bundle(self):
        from .bundle_transfer import export_bundle,parse_bundle,commit_bundle
        owner=get_user_model().objects.create_user(username='长设定来源')
        target=get_user_model().objects.create_user(username='长设定目标')
        policy={'confirmed':True,'core_text':'禁止飞行','segments':{'personality:0:4':{'keywords':['飞行'],'required':True}}}
        actor=Character.objects.create(owner=owner,name='长角色',personality='禁止飞行',context_policy=policy)
        copied=commit_bundle(parse_bundle(export_bundle(owner,[str(actor.pk)],[]),target),target)
        self.assertEqual(Character.objects.get(pk=copied['characters'][0]['id']).context_policy,policy)

    def test_source_edit_requires_new_core_confirmation(self):
        owner=get_user_model().objects.create_user(username='核心确认')
        actor=Character.objects.create(owner=owner,name='角色',personality='原文',context_policy={'confirmed':True,'core_text':'原文'})
        actor.personality='新原文'
        actor.save()
        actor.refresh_from_db()
        self.assertFalse(actor.context_policy.get('confirmed'))

    def test_policy_api_requires_current_revision_and_ownership(self):
        from .models import UserProfile
        from .auth_api import PRIVACY_POLICY_VERSION,USAGE_RULES_VERSION
        from django.utils import timezone
        owner=get_user_model().objects.create_user(username='段落配置')
        UserProfile.objects.update_or_create(user=owner,defaults={'policy_consent_at':timezone.now(),'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        actor=Character.objects.create(owner=owner,name='角色',personality='原文')
        self.client.force_login(owner)
        url=f'/api/characters/{actor.pk}/context/'
        current=self.client.get(url)
        self.assertEqual(current.status_code,200)
        payload={'revision':current.json()['revision'],'policy':{'confirmed':True,'core_text':'原文','segments':{}}}
        self.assertEqual(self.client.patch(url,json.dumps(payload),content_type='application/json').status_code,200)
        self.assertEqual(self.client.patch(url,json.dumps(payload),content_type='application/json').status_code,409)
        other=get_user_model().objects.create_user(username='其他账号')
        UserProfile.objects.update_or_create(user=other,defaults={'policy_consent_at':timezone.now(),'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)
