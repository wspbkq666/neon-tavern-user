from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import UserProfile, UserWarning


class UserWarningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("warning-owner", password="test-password-123")
        UserProfile.objects.create(
            user=self.user,
            policy_consent_at=timezone.now(),
            privacy_policy_version=PRIVACY_POLICY_VERSION,
            usage_rules_version=USAGE_RULES_VERSION,
        )
        self.other = get_user_model().objects.create_user("warning-other", password="test-password-123")
        self.client.force_login(self.user)

    def test_user_can_read_and_acknowledge_only_their_unread_warnings(self):
        own = UserWarning.objects.create(user=self.user, message="请检查你发布的素材。")
        foreign = UserWarning.objects.create(user=self.other, message="不得泄漏给其他账号")

        response = self.client.get("/api/account/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["warnings"]], [own.pk])
        acknowledged = self.client.post(
            "/api/account/warnings/read/",
            data=f'{{"ids":[{own.pk},{foreign.pk}]}}',
            content_type="application/json",
        )

        self.assertEqual(acknowledged.status_code, 200)
        own.refresh_from_db()
        foreign.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertFalse(foreign.is_read)
