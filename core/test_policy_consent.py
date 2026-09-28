import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .views import _page_context


class PolicyConsentTests(TestCase):
    def _register(self, **extra):
        payload = {
            "username": "consent-user",
            "password": "VeryStrongPassword123!",
            **extra,
        }
        return self.client.post(
            "/api/auth/register/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    @override_settings(
        TAVERN_DR_ENABLED=True,
        TAVERN_DR_PEER_URL="",
        TAVERN_DR_STANDBY_ORIGIN="",
    )
    def test_independent_primary_does_not_advertise_cross_site_replication(self):
        context = _page_context()
        self.assertFalse(context["dr_enabled"])
        self.assertEqual(context["dr_standby_status_url"], "")

    @override_settings(
        TAVERN_DR_ENABLED=True,
        TAVERN_DR_PEER_URL="https://backup.example",
        TAVERN_DR_STANDBY_ORIGIN="https://backup.example",
    )
    def test_configured_standby_keeps_disaster_recovery_indicators(self):
        context = _page_context()
        self.assertTrue(context["dr_enabled"])
        self.assertEqual(context["dr_standby_status_url"], "https://backup.example/api/disaster-recovery/status/")

    def test_registration_requires_explicit_current_consent(self):
        response = self._register()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username="consent-user").exists())

    def test_registration_records_policy_versions_after_consent(self):
        response = self._register(
            policy_consent=True,
            privacy_policy_version=PRIVACY_POLICY_VERSION,
            usage_rules_version=USAGE_RULES_VERSION,
        )

        self.assertEqual(response.status_code, 201)
        profile = get_user_model().objects.get(username="consent-user").tavern_profile
        self.assertIsNotNone(profile.policy_consent_at)
        self.assertEqual(profile.privacy_policy_version, PRIVACY_POLICY_VERSION)
        self.assertEqual(profile.usage_rules_version, USAGE_RULES_VERSION)

    def test_existing_user_must_confirm_before_using_account_api(self):
        user = get_user_model().objects.create_user(username="existing", password="VeryStrongPassword123!")
        self.client.force_login(user)

        response = self.client.get("/api/settings/")

        self.assertEqual(response.status_code, 428)
        self.assertTrue(response.json()["policy_consent_required"])

    def test_existing_user_can_accept_current_policy_and_continue(self):
        user = get_user_model().objects.create_user(username="existing", password="VeryStrongPassword123!")
        self.client.force_login(user)
        response = self.client.post(
            "/api/auth/consent/",
            data=json.dumps({
                "policy_consent": True,
                "privacy_policy_version": PRIVACY_POLICY_VERSION,
                "usage_rules_version": USAGE_RULES_VERSION,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["policy_consent_required"])
        self.assertEqual(self.client.get("/api/settings/").status_code, 200)
