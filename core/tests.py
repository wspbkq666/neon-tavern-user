import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import Character, MarketListing, UserProfile, Worldbook, WorldbookEntry


class MarketBundlePublishingTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"

    def create_consented_user(self, username):
        user = get_user_model().objects.create_user(username=username, password="test-password")
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "policy_consent_at": timezone.now(),
                "privacy_policy_version": PRIVACY_POLICY_VERSION,
                "usage_rules_version": USAGE_RULES_VERSION,
            },
        )
        return user

    def test_market_publish_rejects_missing_required_fields_and_empty_bundle(self):
        publisher = self.create_consented_user("required-publisher")
        character = Character.objects.create(owner=publisher, name="测试角色")
        self.client.force_login(publisher)

        base = {
            "kind": "character",
            "scope": "local",
            "source_id": str(character.id),
            "title": "完整素材",
            "description": "必填简介",
            "tags": ["测试"],
            "author_alias": "测试署名",
        }
        missing_scope = {**base}
        missing_scope.pop("scope")
        invalid_payloads = [
            (missing_scope, "素材类型或发布范围无效"),
            ({**base, "description": "  "}, "简介不能为空"),
            ({**base, "tags": ["  "]}, "至少填写一个标签"),
            ({**base, "author_alias": "  "}, "署名不能为空"),
            ({**base, "kind": "bundle", "source_id": None, "character_ids": [], "worldbook_ids": []}, "整合包至少选择一张角色卡或一本世界书"),
        ]

        for payload, expected_error in invalid_payloads:
            with self.subTest(expected_error=expected_error):
                response = self.client.post(
                    "/api/market/listings/",
                    data=json.dumps(payload),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(expected_error, response.json()["error"])

        self.assertEqual(MarketListing.objects.filter(owner=publisher).count(), 0)

    def test_public_bundle_can_be_published_and_imported_by_another_user(self):
        publisher = self.create_consented_user("bundle-publisher")
        recipient = self.create_consented_user("bundle-recipient")
        character = Character.objects.create(owner=publisher, name="雾港向导", summary="熟悉旧城街巷")
        book = Worldbook.objects.create(owner=publisher, name="雾港设定", description="潮湿的港口城市")
        WorldbookEntry.objects.create(worldbook=book, name="旧钟楼", content="钟楼每晚十二点会响十三下。")

        self.client.force_login(publisher)
        publish_response = self.client.post(
            "/api/market/listings/",
            data=json.dumps({
                "kind": "bundle",
                "scope": "public",
                "character_ids": [str(character.id)],
                "worldbook_ids": [str(book.id)],
                "title": "雾港角色与设定",
                "description": "角色卡和世界书整合包",
                "tags": ["雾港"],
                "author_alias": "测试发布者",
            }),
            content_type="application/json",
        )

        self.assertEqual(publish_response.status_code, 201, publish_response.content)
        listing = publish_response.json()
        self.assertEqual(listing["kind"], "bundle")
        self.assertEqual(listing["counts"], {"角色卡": 1, "世界书": 1})

        detail_response = self.client.get(f"/api/market/listings/{listing['id']}/?scope=public")
        self.assertEqual(detail_response.status_code, 200)
        payload = detail_response.json()["payload"]
        self.assertEqual(payload["format"], "neon-tavern-bundle")
        self.assertEqual(payload["worldbooks"][0]["payload"]["entries"][0]["name"], "旧钟楼")

        self.client.force_login(recipient)
        import_response = self.client.post(
            f"/api/market/listings/{listing['id']}/download/",
            data=json.dumps({"scope": "public"}),
            content_type="application/json",
        )

        self.assertEqual(import_response.status_code, 201, import_response.content)
        result = import_response.json()
        self.assertEqual([item["name"] for item in result["characters"]], ["雾港向导"])
        self.assertEqual([item["name"] for item in result["worldbooks"]], ["雾港设定"])
        self.assertTrue(WorldbookEntry.objects.filter(worldbook__owner=recipient, name="旧钟楼").exists())
