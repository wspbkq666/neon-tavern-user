from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .disaster_recovery import Lease, WitnessUnavailable


class DisasterRecoveryStatusApiTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.current_lease = Lease("154", 4, now, now + timedelta(seconds=60), "test-signature")
        self.standby_lease = Lease("123", 5, now, now + timedelta(seconds=60), "test-signature")

    def _status(self, local, authority, *, allowed_origins=None, read_only=False):
        now = datetime.now(timezone.utc)
        witness_client = Mock()
        witness_client.status.return_value = authority
        settings = override_settings(
            TAVERN_DR_ENABLED=True,
            TAVERN_NODE_ID="154",
            TAVERN_DR_STATUS_PATH=Path(self.temp.name) / "missing-status.json",
            TAVERN_DR_STATUS_ALLOWED_ORIGINS=allowed_origins or [],
        )
        with settings, patch("core.disaster_recovery_api.get_local_lease", return_value=local), \
                patch("core.disaster_recovery_api.is_read_only_node", return_value=read_only), \
                patch("core.disaster_recovery_api._witness_client", return_value=witness_client):
            response = self.client.get(
                "/api/disaster-recovery/status/",
                HTTP_ORIGIN="https://backup.example",
            )
        return response, witness_client

    def test_primary_requires_local_lease_to_match_live_witness_epoch(self):
        response, witness = self._status(self.current_lease, self.current_lease)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "primary")
        self.assertEqual(response.json()["authority"], {
            "status": "confirmed",
            "holder_id": "154",
            "epoch": 4,
            "expires_at": int(self.current_lease.expires_at.timestamp()),
        })
        witness.status.assert_called_once_with()

    def test_newer_witness_epoch_prevents_old_node_from_reporting_primary(self):
        response, _ = self._status(self.current_lease, self.standby_lease)

        self.assertNotEqual(response.json()["role"], "primary")
        self.assertEqual(response.json()["authority"]["holder_id"], "123")
        self.assertEqual(response.json()["authority"]["epoch"], 5)

    def test_witness_outage_fails_closed_without_sensitive_authority_data(self):
        witness = Mock()
        witness.status.side_effect = WitnessUnavailable("offline")
        with override_settings(
            TAVERN_DR_ENABLED=True,
            TAVERN_NODE_ID="154",
            TAVERN_DR_STATUS_PATH=Path(self.temp.name) / "missing-status.json",
        ), patch("core.disaster_recovery_api.get_local_lease", return_value=self.current_lease), \
                patch("core.disaster_recovery_api.is_read_only_node", return_value=False), \
                patch("core.disaster_recovery_api._witness_client", return_value=witness):
            response = self.client.get("/api/disaster-recovery/status/")

        payload = response.json()
        self.assertNotEqual(payload["role"], "primary")
        self.assertEqual(payload["authority"]["status"], "unavailable")
        self.assertNotIn("signature", str(payload))
        self.assertNotIn("private_key", str(payload))

    def test_cors_allows_only_exact_configured_origin_and_disables_caching(self):
        response, _ = self._status(
            self.current_lease,
            self.current_lease,
            allowed_origins=["https://backup.example"],
        )
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://backup.example")
        self.assertIn("Origin", response["Vary"].split(", "))
        self.assertEqual(response["Cache-Control"], "no-store")

        with override_settings(
            TAVERN_DR_ENABLED=False,
            TAVERN_DR_STATUS_ALLOWED_ORIGINS=["https://backup.example"],
            TAVERN_DR_STATUS_PATH=Path(self.temp.name) / "missing-status.json",
        ):
            denied = self.client.get(
                "/api/disaster-recovery/status/",
                HTTP_ORIGIN="https://backup.example.evil",
            )
        self.assertNotIn("Access-Control-Allow-Origin", denied)

    def test_status_response_never_contains_user_content_or_session_data(self):
        response, _ = self._status(self.current_lease, self.current_lease)

        payload = response.json()
        self.assertNotIn("messages", payload)
        self.assertNotIn("conversation", payload)
        self.assertNotIn("cookie", payload)
        self.assertNotIn("signature", payload.get("authority", {}))
        self.assertNotIn("Set-Cookie", response)
