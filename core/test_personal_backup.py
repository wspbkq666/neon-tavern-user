import io
import zipfile
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Character
from .personal_backup import serialize_personal_backup,validate_personal_backup,restore_personal_backup


class PersonalBackupTests(TestCase):
    def test_malformed_scalar_and_private_fields_cannot_create_partial_data(self):
        import json
        owner=get_user_model().objects.create_user(username='损坏来源')
        target=get_user_model().objects.create_user(username='损坏目标')
        Character.objects.create(owner=owner,name='完整角色')
        original=serialize_personal_backup(owner)
        with zipfile.ZipFile(io.BytesIO(original)) as package: manifest=json.loads(package.read('manifest.json'))
        manifest['objects'][0]['fields']['name']='超过长度'*100
        invalid=io.BytesIO()
        with zipfile.ZipFile(invalid,'w') as package: package.writestr('manifest.json',json.dumps(manifest))
        from django.core.exceptions import ValidationError
        with self.assertRaises((ValueError,ValidationError)):
            restore_personal_backup(target,validate_personal_backup(target,invalid.getvalue()),'损坏恢复')
        self.assertFalse(Character.objects.filter(owner=target).exists())
        manifest['objects'][0]['fields']['encrypted_api_key']='禁止字段'
        invalid=io.BytesIO()
        with zipfile.ZipFile(invalid,'w') as package: package.writestr('manifest.json',json.dumps(manifest))
        with self.assertRaises(ValueError):validate_personal_backup(target,invalid.getvalue())

    def test_backup_tasks_have_expiry_and_owned_delete(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import PersonalBackupJob,UserProfile
        from .auth_api import PRIVACY_POLICY_VERSION,USAGE_RULES_VERSION
        owner=get_user_model().objects.create_user(username='任务删除用户')
        other=get_user_model().objects.create_user(username='任务其他用户')
        for user in (owner,other):UserProfile.objects.update_or_create(user=user,defaults={'policy_consent_at':timezone.now(),'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        job=PersonalBackupJob.objects.create(owner=owner,kind='export',status='done',package='本地测试'.encode())
        expired=PersonalBackupJob.objects.create(owner=owner,kind='export',status='done',package='过期测试'.encode())
        PersonalBackupJob.objects.filter(pk=expired.pk).update(created_at=timezone.now()-timedelta(days=8))
        self.client.force_login(owner)
        result=self.client.get('/api/personal-backups/')
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(result.json()['jobs']),1)
        self.assertIn('expires_at',result.json()['jobs'][0])
        self.client.force_login(other)
        self.assertEqual(self.client.delete(f'/api/personal-backups/{job.pk}/').status_code,404)
        self.client.force_login(owner)
        self.assertEqual(self.client.delete(f'/api/personal-backups/{job.pk}/').status_code,204)
        self.assertFalse(PersonalBackupJob.objects.filter(pk=job.pk).exists())

    def test_restore_keeps_message_timestamps_and_order(self):
        import uuid
        from datetime import timedelta
        from django.utils import timezone
        from .models import Conversation, Message
        owner=get_user_model().objects.create_user(username='时间来源')
        target=get_user_model().objects.create_user(username='时间目标')
        actor=Character.objects.create(owner=owner,name='时间角色',is_player_controlled=True)
        story=Conversation.objects.create(owner=owner,title='时间故事',player_character=actor)
        first=Message.objects.create(id=uuid.UUID(int=900),conversation=story,kind='dialogue',content='先发生')
        second=Message.objects.create(id=uuid.UUID(int=1),conversation=story,kind='dialogue',content='后发生')
        earlier=timezone.now()-timedelta(days=2)
        later=earlier+timedelta(hours=1)
        Message.objects.filter(pk=first.pk).update(created_at=earlier)
        Message.objects.filter(pk=second.pk).update(created_at=later)
        restore_personal_backup(target,validate_personal_backup(target,serialize_personal_backup(owner)),'时间恢复')
        copied=Message.objects.filter(conversation__owner=target).order_by('created_at','id')
        self.assertEqual(list(copied.values_list('content',flat=True)),['先发生','后发生'])
        self.assertEqual(list(copied.values_list('created_at',flat=True)),[earlier,later])

    def test_restore_adds_material_and_preserves_accounts(self):
        owner=get_user_model().objects.create_user(username='备份来源',password='不会导出')
        target=get_user_model().objects.create_user(username='备份目标',password='不会覆盖')
        original=Character.objects.create(owner=owner,name='角色',personality='完整原文',first_mes='原文开场白')
        package=serialize_personal_backup(owner)
        preview=validate_personal_backup(target,package)
        result=restore_personal_backup(target,preview,'恢复一次')
        copied=Character.objects.get(owner=target)
        self.assertEqual(copied.personality,original.personality)
        self.assertEqual(copied.first_mes,original.first_mes)
        self.assertTrue(target.check_password('不会覆盖'))
        self.assertEqual(restore_personal_backup(target,preview,'恢复一次'),result)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            text=archive.read('manifest.json').decode()
            self.assertNotIn('encrypted_api_key',text)
            self.assertNotIn('不会导出',text)

    def test_unsafe_zip_path_is_rejected(self):
        owner=get_user_model().objects.create_user(username='路径检查')
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('../manifest.json','{}')
        with self.assertRaisesRegex(ValueError,'路径'):
            validate_personal_backup(owner,data.getvalue())

    def test_story_states_memory_worldbook_and_checkpoint_roundtrip(self):
        from .models import Conversation,ConversationParticipant,Worldbook,WorldbookEntry,ConversationWorldbookConfig,Message,StoryCheckpoint
        from .story_state import ensure_story_state,update_story_state
        from .story_snapshots import create_checkpoint
        owner=get_user_model().objects.create_user(username='完整备份来源')
        target=get_user_model().objects.create_user(username='完整备份目标')
        player=Character.objects.create(owner=owner,name='玩家',is_player_controlled=True)
        actor=Character.objects.create(owner=owner,name='NPC',personality='禁止飞行')
        story=Conversation.objects.create(owner=owner,title='剧情',player_character=player,long_memory='事件记忆',locked_facts=[{'id':'规则','text':'不能飞行'}])
        ConversationParticipant.objects.create(conversation=story,character=actor,position=0)
        ConversationParticipant.objects.create(conversation=story,character=player,position=1)
        state=ensure_story_state(story,actor)
        update_story_state(story,actor,{'state_fields':{'心情':'高兴'}},state.revision)
        book=Worldbook.objects.create(owner=owner,name='世界书')
        entry=WorldbookEntry.objects.create(worldbook=book,name='规则',content='不能飞行',trigger_mode='always')
        config=ConversationWorldbookConfig.objects.create(conversation=story)
        config.enabled_worldbooks.add(book)
        Message.objects.create(conversation=story,speaker=actor,kind='dialogue',content='你好',consumed_by=[str(player.pk)])
        create_checkpoint(story,'关键节点')
        result=restore_personal_backup(target,validate_personal_backup(target,serialize_personal_backup(owner)),'完整恢复')
        copied=Conversation.objects.get(owner=target)
        self.assertEqual(copied.long_memory,'事件记忆')
        self.assertEqual(copied.actor_states.get(character__name='NPC').state_fields,{'心情':'高兴'})
        self.assertEqual(copied.messages.get().consumed_by,[str(copied.player_character_id)])
        checkpoint=StoryCheckpoint.objects.get(conversation=copied)
        self.assertEqual(checkpoint.snapshot['source_conversation_id'],str(copied.pk))
        self.assertEqual(copied.worldbook_config.enabled_worldbooks.get().entries.get().content,'不能飞行')
