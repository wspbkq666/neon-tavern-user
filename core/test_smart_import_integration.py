import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from core.models import Character, UserProfile, Worldbook, WorldbookEntry


class SmartImportEndToEndTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="smart-import-e2e", password="unused")
        UserProfile.objects.update_or_create(user=self.user, defaults={
            "policy_consent_at": timezone.now(),
            "privacy_policy_version": PRIVACY_POLICY_VERSION,
            "usage_rules_version": USAGE_RULES_VERSION,
            "inferred_traits": "existing conversation profile",
        })
        self.client.force_login(self.user)

    @patch("core.smart_import_api.active_api_key", return_value="mock-api-key")
    @patch("core.smart_import_api.effective_values", return_value={"model": "integration-fake"})
    @patch("core.smart_import.call_json_model")
    def test_mixed_document_imports_worldbook_npc_and_player_through_bundle_flow(self, model, settings, active_key):
        model.return_value = {"items": [
            {
                "type": "worldbook",
                "fields": {"name": "Fog City", "description": "Rules for the fog city", "entries": [
                    {"name": "Night watch", "content": "The clinic closes at midnight.", "keywords": ["clinic"], "scoped_characters": ["Aya"]},
                ]},
                "source_excerpt": "Fog City rules", "confidence": 0.96, "warnings": [],
            },
            {
                "type": "npc",
                "fields": {"name": "Aya", "description": "A night doctor", "personality": "Calm", "first_mes": "Are you hurt?"},
                "source_excerpt": "Aya is a calm night doctor.", "confidence": 0.91, "warnings": [],
            },
            {
                "type": "player",
                "fields": {"name": "Traveler", "scenario": "Arrived in the city."},
                "source_excerpt": "The player is a traveler.", "confidence": 0.87, "warnings": [],
            },
        ]}
        imported_profile = UserProfile.objects.get(user=self.user)
        source = "# Fog City\nAya is a calm night doctor.\nThe player is a traveler."
        preview_response = self.client.post(
            "/api/smart-import/preview/",
            data=json.dumps({"text": source}),
            content_type="application/json",
        )
        self.assertEqual(preview_response.status_code, 200, preview_response.content)
        smart_preview = preview_response.json()
        self.assertEqual([item["type"] for item in smart_preview["drafts"]], ["worldbook", "npc", "player"])
        self.assertTrue(any("first_mes" in warning for warning in smart_preview["warnings"]))
        self.assertEqual(Character.objects.filter(owner=self.user).count(), 0)
        self.assertEqual(Worldbook.objects.filter(owner=self.user).count(), 0)

        bundle_preview = self.client.post(
            "/api/bundles/import/preview/",
            data=json.dumps({"payload": smart_preview["payload"]}),
            content_type="application/json",
        )
        self.assertEqual(bundle_preview.status_code, 200, bundle_preview.content)
        self.assertEqual(bundle_preview.json()["counts"]["characters"], 2)
        self.assertEqual(bundle_preview.json()["counts"]["worldbooks"], 1)

        commit_response = self.client.post(
            "/api/bundles/import/commit/",
            data=json.dumps({"payload": smart_preview["payload"]}),
            content_type="application/json",
        )
        self.assertEqual(commit_response.status_code, 201, commit_response.content)
        self.assertEqual(Character.objects.filter(owner=self.user).count(), 2)
        self.assertEqual(Worldbook.objects.filter(owner=self.user).count(), 1)
        player = Character.objects.get(owner=self.user, name="Traveler")
        npc = Character.objects.get(owner=self.user, name="Aya")
        self.assertTrue(player.is_player_controlled)
        self.assertFalse(npc.is_player_controlled)
        book = Worldbook.objects.get(owner=self.user, name="Fog City")
        entry = WorldbookEntry.objects.get(worldbook=book, name="Night watch")
        self.assertEqual(list(entry.scoped_characters.values_list("id", flat=True)), [npc.id])
        imported_profile.refresh_from_db()
        self.assertEqual(imported_profile.inferred_traits, "existing conversation profile")
        self.assertEqual(commit_response.json()["warnings"], bundle_preview.json()["warnings"])
