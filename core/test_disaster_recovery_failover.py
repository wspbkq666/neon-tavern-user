import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .disaster_recovery import Lease, WitnessUnavailable
from .disaster_recovery_failover import FailoverError, _witness_client, poll_once, promote_node
from .disaster_recovery_snapshot import SnapshotManifest


class DisasterRecoveryFailoverTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.lease = Lease("123", 9, now, now + timedelta(seconds=60), "signed")
        self.manifest = SnapshotManifest(
            snapshot_id="snapshot-1", source_node_id="154", epoch=8,
            app_version="test-app", migration_versions=("core:0001_initial",),
            created_at=now.isoformat(), database_sha256="a" * 64,
            media_files={}, encryption_key_version="key-1", signature="signed",
        )

    def test_promotion_requires_fresh_validated_snapshot_and_higher_epoch(self):
        with override_settings(
            TAVERN_NODE_ID="123",
            TAVERN_DR_READ_ONLY=True,
            TAVERN_DR_ROLE_PATH=self.root / "role.json",
            TAVERN_DR_LEASE_PATH=str(self.root / "lease.json"),
            TAVERN_DR_REPLICA_ROOT=self.root / "replica",
            TAVERN_APP_VERSION="test-app",
            TAVERN_DR_ENCRYPTION_KEY_VERSION="key-1",
        ), patch("core.disaster_recovery_failover._validate_replica_snapshot", return_value=self.manifest):
            result = promote_node("123", self.lease, self.manifest)

        self.assertTrue(result["write_enabled"])
        self.assertEqual(result["epoch"], 9)
        self.assertEqual(json.loads((self.root / "role.json").read_text())["role"], "primary")

    def test_lower_epoch_cannot_promote(self):
        older = Lease("123", 8, self.lease.issued_at, self.lease.expires_at, "signed")
        with override_settings(
            TAVERN_NODE_ID="123",
            TAVERN_DR_READ_ONLY=True,
            TAVERN_DR_ROLE_PATH=self.root / "role.json",
            TAVERN_DR_LEASE_PATH=str(self.root / "lease.json"),
            TAVERN_DR_REPLICA_ROOT=self.root / "replica",
            TAVERN_APP_VERSION="test-app",
            TAVERN_DR_ENCRYPTION_KEY_VERSION="key-1",
        ), patch("core.disaster_recovery_failover._validate_replica_snapshot", return_value=self.manifest):
            with self.assertRaises(FailoverError):
                promote_node("123", older, self.manifest)

        self.assertFalse((self.root / "role.json").exists())

    def test_witness_outage_fails_node_closed(self):
        client = Mock()
        client.status.side_effect = WitnessUnavailable("offline")
        with override_settings(
            TAVERN_NODE_ID="154",
            TAVERN_DR_ROLE_PATH=self.root / "role.json",
            TAVERN_DR_READ_ONLY=False,
        ):
            result = poll_once(client)

        self.assertEqual(result["role"], "unavailable")
        self.assertEqual(json.loads((self.root / "role.json").read_text())["role"], "read_only")

    def test_witness_client_factory_passes_dedicated_ca_bundle(self):
        with override_settings(
            TAVERN_NODE_ID="154",
            TAVERN_WITNESS_URL="https://witness.example:43111",
            TAVERN_DR_NODE_PRIVATE_KEY=base64.urlsafe_b64encode(b"p" * 32).decode().rstrip("="),
            TAVERN_WITNESS_PUBLIC_KEY=base64.urlsafe_b64encode(b"q" * 32).decode().rstrip("="),
            TAVERN_WITNESS_CA_BUNDLE="/etc/neon-tavern/witness-ca.pem",
        ), patch("core.disaster_recovery_failover.WitnessClient") as client_factory:
            _witness_client()

        self.assertEqual(client_factory.call_args.kwargs["ca_bundle"], "/etc/neon-tavern/witness-ca.pem")
