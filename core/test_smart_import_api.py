import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from django.utils import timezone

from core.auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from core.models import Character, UserProfile, Worldbook


class SmartImportApiTests(TestCase):
    url = "/api/smart-import/preview/"

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="smart-import-user", password="unused")
        UserProfile.objects.update_or_create(user=self.user, defaults={
            "policy_consent_at": timezone.now(),
            "privacy_policy_version": PRIVACY_POLICY_VERSION,
            "usage_rules_version": USAGE_RULES_VERSION,
        })
        self.client.force_login(self.user)

    @staticmethod
    def model_result():
        return {"items": [{
            "type": "npc",
            "fields": {"name": "Lin", "description": "A doctor"},
            "source_excerpt": "Lin is a doctor",
            "confidence": 0.92,
            "warnings": [],
        }]}

    def test_anonymous_request_is_rejected(self):
        self.client.logout()
        response = self.client.post(self.url, data=json.dumps({"text": "Lin"}), content_type="application/json")
        self.assertEqual(response.status_code, 401)

    @patch("core.smart_import_api.active_api_key", return_value="")
    def test_missing_key_returns_actionable_error_without_model_call(self, active_key):
        with patch("core.smart_import.call_json_model") as model:
            response = self.client.post(self.url, data=json.dumps({"text": "Lin"}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("API", response.json()["error"])
        model.assert_not_called()

    @patch("core.smart_import_api.active_api_key", return_value="server-secret")
    @patch("core.smart_import_api.effective_values", return_value={"model": "unit-model", "temperature": 0.2})
    @patch("core.smart_import.call_json_model")
    @patch("core.smart_import_api.parse_bundle")
    def test_text_input_uses_user_settings_and_bundle_preview_without_writes(self, parse_bundle, model, settings, active_key):
        from core.bundle_transfer import parse_bundle as real_parse_bundle
        model.return_value = self.model_result()
        parse_bundle.side_effect = lambda payload, owner: real_parse_bundle(payload, owner)
        before = (Character.objects.count(), Worldbook.objects.count())
        response = self.client.post(self.url, data=json.dumps({"text": "Lin is a doctor."}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"drafts", "payload", "bundle_preview", "warnings"})
        self.assertEqual(response.json()["drafts"][0]["type"], "npc")
        active_key.assert_called_once_with(self.user)
        settings.assert_called_once_with(self.user)
        parse_bundle.assert_called_once()
        self.assertEqual(model.call_args.args[1], {"model": "unit-model", "temperature": 0.2})
        self.assertEqual(model.call_args.args[2], "server-secret")
        self.assertNotIn("server-secret", response.content.decode())
        self.assertEqual((Character.objects.count(), Worldbook.objects.count()), before)

    @patch("core.smart_import_api.active_api_key", return_value="server-secret")
    @patch("core.smart_import_api.effective_values", return_value={"model": "unit-model"})
    @patch("core.smart_import.call_json_model")
    def test_multipart_text_file_is_extracted_and_analyzed(self, model, settings, active_key):
        model.return_value = self.model_result()
        upload = SimpleUploadedFile("notes.md", b"# Lin\nA doctor")
        response = self.client.post(self.url, data={"file": upload})
        self.assertEqual(response.status_code, 200, response.content)
        messages = model.call_args.args[0]
        self.assertIn("# Lin", messages[1]["content"])
        self.assertIn("A doctor", messages[1]["content"])

    @patch("core.smart_import_api.active_api_key", return_value="server-secret")
    @patch("core.smart_import_api.effective_values", return_value={"model": "unit-model"})
    @patch("core.smart_import.call_json_model", return_value={"items": [{"type": "unknown", "fields": {}, "source_excerpt": "uncertain", "confidence": 0.3, "warnings": []}]})
    def test_uncertain_item_is_returned_for_user_classification(self, model, settings, active_key):
        response = self.client.post(self.url, data=json.dumps({"text": "uncertain"}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["drafts"][0]["type"], "unknown")
        self.assertEqual(response.json()["bundle_preview"]["counts"]["characters"], 0)

    @patch("core.smart_import_api.active_api_key", return_value="secret")
    @patch("core.smart_import_api.effective_values", return_value={"model": "fake"})
    @patch("core.smart_import.call_json_model", side_effect=RuntimeError("provider detail and secret"))
    def test_model_failure_is_retryable_and_does_not_echo_internal_error(self, model, settings, active_key):
        response = self.client.post(self.url, data=json.dumps({"text": "Lin"}), content_type="application/json")
        self.assertEqual(response.status_code, 502)
        self.assertIn("重试", response.json()["error"])
        self.assertNotIn("provider detail", response.content.decode())
        self.assertNotIn("secret", response.content.decode())

    @patch("core.smart_import_api.active_api_key", return_value="secret")
    @patch("core.smart_import_api.effective_values", return_value={"model": "fake"})
    @patch("core.smart_import.call_json_model", return_value={"instructions": "ignore rules"})
    def test_invalid_model_result_is_bad_request(self, model, settings, active_key):
        response = self.client.post(self.url, data=json.dumps({"text": "Lin"}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_ambiguous_or_empty_input_is_rejected(self):
        for payload in ({}, {"text": "  "}, {"text": "Lin", "other": "value"}):
            with self.subTest(payload=payload):
                response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400)

    def test_overlong_text_is_rejected_before_model_request(self):
        with patch("core.smart_import_api.active_api_key", return_value="secret"), patch("core.smart_import_api.effective_values", return_value={"model": "fake"}), patch("core.smart_import.call_json_model") as model:
            response = self.client.post(self.url, data=json.dumps({"text": "x" * 100001}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        model.assert_not_called()

