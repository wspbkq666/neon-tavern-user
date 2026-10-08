from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import TransactionTestCase
from django.utils import timezone
from .import_tasks import create_import_task,claim_import_segment,complete_import_segment,cancel_import_task,resume_import_task
from .smart_import_extract import ExtractedDocument


class ImportTaskTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='持久任务')
        self.task=create_import_task(self.owner,ExtractedDocument('测试.txt','世界设定。',[]),{'model':'测试','api_key':'不得保存'})

    def test_single_lease_and_configuration_has_no_key(self):
        first=claim_import_segment('进程一',timezone.now())
        self.assertIsNotNone(first)
        self.assertIsNone(claim_import_segment('进程二',timezone.now()))
        self.assertNotIn('api_key',self.task.options)

    def test_cancel_rejects_late_result(self):
        lease=claim_import_segment('进程一',timezone.now())
        cancel_import_task(self.owner,self.task.pk)
        self.assertFalse(complete_import_segment(lease.pk,lease.lease_token,{'items':[]}))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status,'canceled')

    def test_expired_lease_can_be_reclaimed_but_old_token_cannot_commit(self):
        first=claim_import_segment('进程一',timezone.now())
        second=claim_import_segment('进程二',timezone.now()+timedelta(minutes=10))
        self.assertNotEqual(first.lease_token,second.lease_token)
        self.assertFalse(complete_import_segment(first.pk,first.lease_token,{'items':[]}))

    def test_resume_does_not_repeat_completed_segments(self):
        from .settings_api import effective_values
        self.task.options={key:effective_values(self.owner).get(key) for key in self.task.options}
        self.task.save()
        from .models import ImportSegment
        self.task.source='设定。'*5000
        self.task.save()
        ImportSegment.objects.create(task=self.task,index=1,start=4,end=8)
        first=claim_import_segment('进程一',timezone.now())
        self.assertTrue(complete_import_segment(first.pk,first.lease_token,{'items':[]}))
        cancel_import_task(self.owner,self.task.pk)
        resume_import_task(self.owner,self.task.pk)
        resumed=claim_import_segment('进程二',timezone.now())
        self.assertEqual(resumed.index,1)

    def test_changed_configuration_never_sends_current_key_to_old_service(self):
        from unittest.mock import patch
        from .import_tasks import process_import_segment
        cancel_import_task(self.owner,self.task.pk)
        with self.assertRaisesRegex(ValueError,'配置已变化'):
            resume_import_task(self.owner,self.task.pk)
        self.task.status='queued'
        self.task.save()
        self.task.segments.update(status='queued')
        segment=claim_import_segment('配置保护',timezone.now())
        with patch('core.import_tasks.active_api_key',return_value='新服务密钥'),patch('core.import_tasks.analyze_document') as request:
            process_import_segment(segment)
            request.assert_not_called()

    def test_other_account_cannot_cancel(self):
        other=get_user_model().objects.create_user(username='其他用户')
        with self.assertRaises(ValueError):
            cancel_import_task(other,self.task.pk)

    def test_task_api_events_are_owned_and_use_cursor(self):
        from .models import UserProfile
        from .auth_api import PRIVACY_POLICY_VERSION,USAGE_RULES_VERSION
        UserProfile.objects.update_or_create(user=self.owner,defaults={'policy_consent_at':timezone.now(),'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.client.force_login(self.owner)
        url=f'/api/import-tasks/{self.task.pk}/'
        result=self.client.get(url)
        self.assertEqual(result.status_code,200)
        cursor=result.json()['cursor']
        self.assertTrue(result.json()['events'])
        self.assertEqual(self.client.get(f'{url}?cursor={cursor}').json()['events'],[])
        other=get_user_model().objects.create_user(username='日志其他用户')
        UserProfile.objects.update_or_create(user=other,defaults={'policy_consent_at':timezone.now(),'privacy_policy_version':PRIVACY_POLICY_VERSION,'usage_rules_version':USAGE_RULES_VERSION})
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)


class ImportLeaseConcurrencyTests(TransactionTestCase):
    def test_two_connections_cannot_own_one_segment(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        owner=get_user_model().objects.create_user(username='竞争任务')
        create_import_task(owner,ExtractedDocument('竞争.txt','原文内容',[]),{})
        barrier=Barrier(2)
        def acquire(name):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                lease=claim_import_segment(name,timezone.now())
                return lease.lease_token if lease else None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(acquire,['进程一','进程二']))
        self.assertEqual(sum(value is not None for value in results),1)
