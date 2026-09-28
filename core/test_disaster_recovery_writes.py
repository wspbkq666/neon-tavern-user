import json
import base64
import sqlite3
import tempfile
import threading
import time
from datetime import timedelta
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from .disaster_recovery import Lease, WriteLeaseUnavailable, canonical_lease_payload, set_local_role
from .disaster_recovery_snapshot import snapshot_write_lock
from .models import Character


@override_settings(TAVERN_DR_ENABLED=True, TAVERN_NODE_ID="154", TAVERN_DR_LEASE_PATH="")
class DisasterRecoveryWriteGateTests(TestCase):
    def test_status_endpoint_reports_read_only_node_without_private_data(self):
        response = self.client.get("/api/disaster-recovery/status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["node_id"], "154")
        self.assertIn(response.json()["role"], {"read_only", "unavailable"})
        self.assertNotIn("secret", json.dumps(response.json()).lower())

    def test_unsafe_api_request_fails_closed_without_a_valid_lease(self):
        response = self.client.post(
            f"/api/conversations/{uuid.uuid4()}/generate/",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error_code"], "write_lease_unavailable")

    def test_configured_standby_never_reports_itself_as_primary(self):
        with override_settings(TAVERN_DR_READ_ONLY=True):
            response = self.client.get("/api/disaster-recovery/status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "read_only")

    def test_replication_lag_over_one_hour_is_reported_as_stale(self):
        from datetime import datetime, timezone

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        status_path = Path(temp.name) / "status.json"
        last_success = datetime.now(timezone.utc) - timedelta(seconds=3700)
        status_path.write_text(json.dumps({
            "status": "healthy", "last_successful_replication": last_success.isoformat(),
            "snapshot_created_at": last_success.isoformat(),
        }), encoding="utf-8")
        with override_settings(TAVERN_DR_STATUS_PATH=status_path):
            response = self.client.get("/api/disaster-recovery/status/")

        self.assertEqual(response.json()["replica_status"], "stale")
        self.assertGreater(response.json()["replication_lag_seconds"], 3600)


class DisasterRecoveryDatabaseGuardTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("dr-writer", password="Strong-passphrase-42")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def _lease(self, *, epoch=1):
        unsigned = Lease("154", epoch, self.now, self.now + timedelta(seconds=60), "pending")
        signature = base64.urlsafe_b64encode(self.private_key.sign(canonical_lease_payload(unsigned))).decode("ascii").rstrip("=")
        return Lease(unsigned.holder_id, unsigned.epoch, unsigned.issued_at, unsigned.expires_at, signature)

    def _dr_settings(self, *, lease=None, read_only=False, lock_path=None):
        lease_path = Path(self.temp_dir.name) / "lease.json"
        if lease is not None:
            from .disaster_recovery import lease_to_dict

            lease_path.write_text(json.dumps(lease_to_dict(lease)), encoding="utf-8")
        raw_public_key = self.public_key.public_bytes_raw()
        public_key = base64.urlsafe_b64encode(raw_public_key).decode("ascii").rstrip("=")
        return override_settings(
            TAVERN_DR_ENABLED=True,
            TAVERN_NODE_ID="154",
            TAVERN_DR_READ_ONLY=read_only,
            TAVERN_DR_LEASE_PATH=str(lease_path),
            TAVERN_DR_SNAPSHOT_LOCK_PATH=str(lock_path or (Path(self.temp_dir.name) / "snapshot.lock")),
            TAVERN_DR_ROLE_PATH=Path(self.temp_dir.name) / "role.json",
            TAVERN_WITNESS_PUBLIC_KEY=public_key,
        )

    def test_database_write_is_rejected_without_local_lease(self):
        with self._dr_settings():
            connection.close()
            with self.assertRaises(WriteLeaseUnavailable):
                Character.objects.create(owner=self.user, name="must-not-save")

        self.assertFalse(Character.objects.filter(name="must-not-save").exists())

    def test_database_write_commits_with_valid_lease(self):
        lease = self._lease()
        with self._dr_settings(lease=lease):
            connection.close()
            character = Character.objects.create(owner=self.user, name="synthetic")

        self.assertTrue(Character.objects.filter(pk=character.pk).exists())

    def test_transaction_rolls_back_if_lease_is_lost_before_commit(self):
        lease = self._lease()
        with self._dr_settings(lease=lease):
            connection.close()
            with patch("core.disaster_recovery.get_local_lease", side_effect=[lease, None]):
                with self.assertRaises(WriteLeaseUnavailable):
                    with transaction.atomic():
                        Character.objects.create(owner=self.user, name="raced-write")

        self.assertFalse(Character.objects.filter(name="raced-write").exists())

    def test_database_write_waits_for_snapshot_freeze_lock(self):
        lease = self._lease()
        lock_path = Path(self.temp_dir.name) / "snapshot.lock"
        finished = threading.Event()
        errors = []

        def write_character():
            try:
                Character.objects.create(owner_id=self.user.pk, name="after-snapshot")
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with self._dr_settings(lease=lease, lock_path=lock_path):
            connection.close()
            with snapshot_write_lock(lock_path, timeout_seconds=2):
                thread = threading.Thread(target=write_character)
                thread.start()
                time.sleep(0.15)
                self.assertFalse(finished.is_set())
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(Character.objects.filter(name="after-snapshot").exists())

    def test_read_only_connection_rejects_raw_sql_as_a_backstop(self):
        character = Character.objects.create(owner=self.user, name="readonly")
        with self._dr_settings(read_only=True):
            raw_connection = connection.get_new_connection(connection.get_connection_params())
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    raw_connection.execute(
                    f'UPDATE "{Character._meta.db_table}" SET name = ? WHERE id = ?',
                    ("changed", str(character.id)),
                    )
            finally:
                raw_connection.close()

    def test_role_change_reopens_worker_connection_on_replica_database(self):
        lease = self._lease()
        with self._dr_settings(lease=lease, read_only=True):
            connection.close()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            set_local_role("primary")
            character = Character.objects.create(owner=self.user, name="promoted-node-write")

        self.assertTrue(Character.objects.filter(pk=character.pk).exists())
