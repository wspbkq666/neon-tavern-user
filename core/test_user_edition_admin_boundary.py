from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import UserProfile


class UserEditionAdminBoundaryTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="site-admin", password="test-password-123", is_staff=True
        )
        self.accept_current_policies(self.admin)
        self.client.force_login(self.admin)

    @staticmethod
    def accept_current_policies(user):
        UserProfile.objects.create(
            user=user,
            policy_consent_at=timezone.now(),
            privacy_policy_version=PRIVACY_POLICY_VERSION,
            usage_rules_version=USAGE_RULES_VERSION,
        )

    def test_global_update_policy_endpoint_is_not_shipped(self):
        response = self.client.get("/api/update-policy/")

        self.assertEqual(response.status_code, 404)

    def test_site_admin_defaults_remain_available_without_global_publish_data(self):
        response = self.client.get("/api/admin/defaults/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("defaults", response.json())
        self.assertNotIn("update_policy", response.json())

    def test_regular_users_cannot_edit_site_defaults(self):
        regular_user = get_user_model().objects.create_user(
            username="ordinary-user", password="test-password-123"
        )
        self.accept_current_policies(regular_user)
        self.client.force_login(regular_user)

        response = self.client.put(
            "/api/admin/defaults/",
            data='{"model":"deepseek-flash"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_site_admin_can_read_dual_center_health_but_cannot_reconfigure_it(self):
        status = self.client.get("/api/federation/status/")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            {target["target_key"] for target in status.json()["targets"]},
            {"main_154", "main_123"},
        )
        self.assertEqual(self.client.post("/api/federation/register/").status_code, 404)
