import base64
import os
import tempfile
import uuid
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
from .models import Character, Conversation, FederationIdentity, Message, UserProfile


CENTERS = {
    "main_154": "https://154.222.26.47",
    "main_123": "https://123.56.125.209",
}


def _public_key(private_key):
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@override_settings(DEBUG=True)
class DualCentralReportingTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.identities = {}
        self.private_keys = {}
        env = {}
        for target_key, central_url, env_name in (
            ("main_154", CENTERS["main_154"], "TAVERN_FEDERATION_PRIVATE_KEY_FILE"),
            ("main_123", CENTERS["main_123"], "TAVERN_FEDERATION_MAIN_123_PRIVATE_KEY_FILE"),
        ):
            private_key = Ed25519PrivateKey.generate()
            key_path = os.path.join(self.temp.name, f"{target_key}.pem")
            with open(key_path, "wb") as handle:
                handle.write(private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ))
            env[env_name] = key_path
            self.private_keys[target_key] = private_key
            identity = FederationIdentity.objects.create(
                target_key=target_key,
                site_id=uuid.uuid4(),
                central_url=central_url,
                central_public_key="A" * 43,
                private_key_env_name=env_name,
            )
            self.identities[target_key] = identity
        env["TAVERN_FEDERATION_SITE_KEY"] = "test.example"
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        self.user = get_user_model().objects.create_user("mirror-test", password="long-test-password")
        self.profile = UserProfile.objects.create(
            user=self.user,
            site_key="test.example",
            policy_consent_at=timezone.now(),
            privacy_policy_version=PRIVACY_POLICY_VERSION,
            usage_rules_version=USAGE_RULES_VERSION,
        )
        from .user_directory_sync import queue_user_directory_event
        queue_user_directory_event(self.user)
        self.character = Character.objects.create(owner=self.user, name="测试角色")
        self.conversation = Conversation.objects.create(
            owner=self.user, title="双总站测试", player_character=self.character,
        )

    def test_chat_and_user_directory_are_sent_to_both_centers_independently(self):
        from .chat_sync_client import sync_once
        from .models import ChatSyncOutbox, UserDirectoryOutbox
        from .user_directory_sync_client import sync_user_directory_once

        message = Message.objects.create(
            conversation=self.conversation, speaker=self.character,
            kind=Message.DIALOGUE, content="同步测试正文", source="user",
        )
        calls = []

        def respond(request):
            calls.append((request.url.host, request.url.path))
            if request.url.path.endswith("chat-events/"):
                import json
                body = json.loads(request.content)
                return httpx.Response(200, json={
                    "cursor": body["cursor"],
                    "accepted": [event["payload"]["source_message_id"] for event in body["events"]],
                })
            import json
            body = json.loads(request.content)
            return httpx.Response(200, json={"cursor": body["cursor"]})

        transport = httpx.MockTransport(respond)
        chat_result = sync_once(transport=transport)
        directory_result = sync_user_directory_once(transport=transport)

        self.assertEqual({host for host, _ in calls}, {"154.222.26.47", "123.56.125.209"})
        self.assertEqual(len(calls), 4)
        self.assertEqual(chat_result["pending"], 0)
        self.assertEqual(directory_result["pending"], 0)
        self.assertFalse(ChatSyncOutbox.objects.filter(source_message_id=str(message.pk)).exists())
        self.assertFalse(UserDirectoryOutbox.objects.filter(source_user_id=str(self.user.pk)).exists())

    def test_message_is_not_queued_without_current_privacy_consent(self):
        from .models import ChatSyncOutbox, UserDirectoryOutbox

        self.profile.privacy_policy_version = "old-policy"
        self.profile.save(update_fields=["privacy_policy_version"])
        message = Message.objects.create(
            conversation=self.conversation, speaker=self.character,
            kind=Message.DIALOGUE, content="不得同步", source="user",
        )

        self.assertFalse(ChatSyncOutbox.objects.filter(source_message_id=str(message.pk)).exists())
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        from .user_directory_sync_client import sync_user_directory_once
        sync_user_directory_once(transport=httpx.MockTransport(lambda request: self.fail("撤回同意后不得同步账号目录")))
        self.assertFalse(UserDirectoryOutbox.objects.filter(source_user_id=str(self.user.pk)).exists())

    def test_each_center_has_an_independent_pending_queue(self):
        from .models import ChatSyncOutbox

        message = Message.objects.create(
            conversation=self.conversation, speaker=self.character,
            kind=Message.DIALOGUE, content="分站故障重试", source="user",
        )

        pending_sites = set(ChatSyncOutbox.objects.filter(
            source_message_id=str(message.pk),
        ).values_list("site_id", flat=True))
        self.assertEqual(pending_sites, {identity.site_id for identity in self.identities.values()})

    def test_both_fixed_centers_are_required_for_registration(self):
        from .site_federation_client import dual_federation_registered

        self.assertTrue(dual_federation_registered())
        self.identities["main_123"].active = False
        self.identities["main_123"].save(update_fields=["active"])
        self.assertFalse(dual_federation_registered())

    def test_site_command_receipt_is_idempotent_and_bound_to_site(self):
        from .site_command_executor import apply_site_command
        from .site_command_signing import canonical_payload
        from .models import SiteCommandReceipt

        command_id = str(uuid.uuid4())
        identity = self.identities["main_154"]
        payload = {
            "command_id": command_id,
            "site_id": str(identity.site_id),
            "target_source_user_id": str(self.user.pk),
            "target_username": self.user.username,
            "action": "warn",
            "reason": "集成测试警告",
            "issued_at": timezone.now().isoformat(),
            "expires_at": (timezone.now() + timezone.timedelta(minutes=5)).isoformat(),
        }
        signature = base64.urlsafe_b64encode(self.private_keys["main_154"].sign(canonical_payload(payload))).decode("ascii").rstrip("=")
        from cryptography.hazmat.primitives import serialization
        public_key = self.private_keys["main_154"].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        identity.central_public_key = base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")
        identity.save(update_fields=["central_public_key"])
        envelope = {"payload": payload, "signature": signature}

        first = apply_site_command(envelope, identity=identity)
        second = apply_site_command(envelope, identity=identity)

        self.assertEqual(first["status"], "executed")
        self.assertEqual(second["status"], "executed")
        self.assertEqual(SiteCommandReceipt.objects.filter(command_id=command_id).count(), 1)

    def test_signed_site_revocation_disables_only_its_own_center_identity(self):
        from .site_command_executor import apply_site_command
        from .site_command_signing import canonical_payload

        identity = self.identities["main_154"]
        payload = {
            "command_id": str(uuid.uuid4()),
            "site_id": str(identity.site_id),
            "target_source_user_id": "",
            "target_username": "",
            "action": "revoke_site",
            "reason": "集成测试撤销",
            "issued_at": timezone.now().isoformat(),
            "expires_at": (timezone.now() + timezone.timedelta(minutes=5)).isoformat(),
        }
        public_key = self.private_keys["main_154"].public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        identity.central_public_key = base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")
        identity.save(update_fields=["central_public_key"])
        signature = base64.urlsafe_b64encode(
            self.private_keys["main_154"].sign(canonical_payload(payload)),
        ).decode("ascii").rstrip("=")

        result = apply_site_command({"payload": payload, "signature": signature}, identity=identity)

        self.assertEqual(result["status"], "executed")
        self.identities["main_154"].refresh_from_db()
        self.identities["main_123"].refresh_from_db()
        self.assertFalse(self.identities["main_154"].active)
        self.assertTrue(self.identities["main_123"].active)
