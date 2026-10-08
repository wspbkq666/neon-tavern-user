from django.contrib.auth import get_user_model
from django.test import TestCase
from .import_history import commit_import_batch,preview_import_undo,undo_import_batch
from .models import Character


class ImportHistoryTests(TestCase):
    def test_retained_modified_worldbook_keeps_its_scoped_character(self):
        from .models import Worldbook,WorldbookEntry
        def operation():
            actor=Character.objects.create(owner=self.owner,name='世界书角色')
            book=Worldbook.objects.create(owner=self.owner,name='作用范围世界书')
            entry=WorldbookEntry.objects.create(worldbook=book,name='条目',content='原设定',scope_type='character')
            entry.scoped_characters.add(actor)
            return {'characters':[{'id':str(actor.pk)}],'worldbooks':[{'id':str(book.pk)}]}
        result=commit_import_batch(self.owner,{'本地测试':'作用范围保护'},'作用范围一次',operation=operation)
        book=Worldbook.objects.get(owner=self.owner);book.entries.update(content='后来增加的设定')
        preview=preview_import_undo(self.owner,result['batch_id'])
        self.assertEqual({row['kind'] for row in preview['retained']},{'character','worldbook'})
        undo_import_batch(self.owner,result['batch_id'],preview['revision'])
        self.assertTrue(book.entries.get().scoped_characters.exists())

    def test_native_character_import_is_idempotent_and_has_history(self):
        import json
        from django.utils import timezone
        from .models import UserProfile,ImportBatch
        from .auth_api import PRIVACY_POLICY_VERSION,USAGE_RULES_VERSION
        UserProfile.objects.update_or_create(user=self.owner,defaults={'policy_consent_at':timezone.now(),'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.client.force_login(self.owner)
        data={'characters':[{'name':'普通导入角色','personality':'禁止飞行'}],'idempotency_key':'普通导入一次'}
        first=self.client.post('/api/characters/import/commit/',json.dumps(data),content_type='application/json')
        second=self.client.post('/api/characters/import/commit/',json.dumps(data),content_type='application/json')
        self.assertEqual(first.status_code,201)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(ImportBatch.objects.filter(owner=self.owner).count(),1)

    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='历史用户')
        self.payload={'format':'neon-tavern-bundle','version':2,'characters':[{'package_id':'一','name':'新角色'}],'worldbooks':[]}

    def test_same_key_creates_one_batch(self):
        first=commit_import_batch(self.owner,self.payload,'一次提交')
        second=commit_import_batch(self.owner,self.payload,'一次提交')
        self.assertEqual(first,second)
        self.assertEqual(Character.objects.filter(owner=self.owner).count(),1)

    def test_changed_material_survives_undo(self):
        result=commit_import_batch(self.owner,self.payload,'修改素材')
        actor=Character.objects.get(pk=result['characters'][0]['id'])
        actor.personality='用户新设定'
        actor.save()
        preview=preview_import_undo(self.owner,result['batch_id'])
        self.assertTrue(preview['retained'])
        undone=undo_import_batch(self.owner,result['batch_id'],preview['revision'])
        self.assertTrue(Character.objects.filter(pk=actor.pk).exists())
        self.assertTrue(undone['retained'])

    def test_unused_batch_undo_is_idempotent(self):
        result=commit_import_batch(self.owner,self.payload,'未使用')
        preview=preview_import_undo(self.owner,result['batch_id'])
        first=undo_import_batch(self.owner,result['batch_id'],preview['revision'])
        second=undo_import_batch(self.owner,result['batch_id'],preview['revision'])
        self.assertEqual(first,second)
        self.assertFalse(Character.objects.filter(owner=self.owner).exists())
