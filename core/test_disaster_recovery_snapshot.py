import base64
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import SimpleTestCase, override_settings

from .disaster_recovery import Lease
from .disaster_recovery_snapshot import SnapshotValidationError, create_snapshot, install_snapshot, snapshot_write_lock, validate_snapshot


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class DisasterRecoverySnapshotTests(SimpleTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "source.sqlite3"
        self.media = self.root / "media"
        self.media.mkdir()
        db = sqlite3.connect(self.database)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO sample(value) VALUES ('合成数据')")
            db.commit()
        finally:
            db.close()
        (self.media / "avatar.bin").write_bytes(b"synthetic-media\x00bytes")
        self.witness_key = Ed25519PrivateKey.generate()
        self.node_key = Ed25519PrivateKey.generate()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.lease = self._signed_lease()
        self.encryption_key = _b64(b"A" * 32)
        self.node_public = _b64(self.node_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.witness_public = _b64(self.witness_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.settings = override_settings(
            TAVERN_NODE_ID="154",
            TAVERN_APP_VERSION="test-build-1",
            TAVERN_DR_ENCRYPTION_KEY=self.encryption_key,
            TAVERN_DR_ENCRYPTION_KEY_VERSION="test-key-v1",
            TAVERN_DR_NODE_PRIVATE_KEY=_b64(self.node_key.private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
            )),
            TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS=json.dumps({"154": self.node_public}),
            TAVERN_WITNESS_PUBLIC_KEY=self.witness_public,
            TAVERN_DR_SNAPSHOT_LOCK_PATH=str(self.root / "snapshot.lock"),
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def _signed_lease(self, epoch=7):
        from .disaster_recovery import canonical_lease_payload

        expires_at = self.now + timedelta(seconds=60)
        unsigned = Lease("154", epoch, self.now, expires_at, "pending")
        signature = _b64(self.witness_key.sign(canonical_lease_payload(unsigned)))
        return Lease("154", epoch, self.now, expires_at, signature)

    def _create(self, name="source.drsnap"):
        return create_snapshot(
            self.root / name,
            self.lease,
            source_db=self.database,
            media_root=self.media,
            freeze_timeout_seconds=2,
        )

    def test_snapshot_contains_consistent_sqlite_backup_and_media_hashes(self):
        package = self._create()

        manifest = validate_snapshot(package, expected_version="test-build-1")

        self.assertEqual(manifest.source_node_id, "154")
        self.assertEqual(manifest.epoch, 7)
        self.assertEqual(manifest.app_version, "test-build-1")
        self.assertEqual(manifest.media_files["avatar.bin"]["sha256"], hashlib.sha256(b"synthetic-media\x00bytes").hexdigest())
        install_snapshot(package, self.root / "replica", expected_version="test-build-1")
        active = self.root / "replica" / "active"
        db = sqlite3.connect(active / "database.sqlite3")
        try:
            self.assertEqual(db.execute("SELECT value FROM sample").fetchone()[0], "合成数据")
        finally:
            db.close()
        self.assertEqual((active / "media" / "avatar.bin").read_bytes(), b"synthetic-media\x00bytes")

    def test_snapshot_lock_blocks_database_writer_until_capture_finishes(self):
        started = threading.Event()
        finished = threading.Event()
        errors = []

        def writer():
            started.set()
            try:
                with snapshot_write_lock(self.root / "snapshot.lock", timeout_seconds=2):
                    finished.set()
            except Exception as exc:
                errors.append(exc)

        with snapshot_write_lock(self.root / "snapshot.lock", timeout_seconds=2):
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(started.wait(timeout=1))
            time.sleep(0.15)
            self.assertFalse(finished.is_set())
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(finished.is_set())

    def test_corrupt_or_wrong_key_snapshot_never_replaces_active_copy(self):
        package = self._create()
        target = self.root / "replica"
        active = target / "active"
        active.mkdir(parents=True)
        marker = active / "last-good.txt"
        marker.write_text("preserve", encoding="utf-8")
        corrupted = self.root / "corrupted.drsnap"
        raw = bytearray(package.read_bytes())
        raw[-1] ^= 0x01
        corrupted.write_bytes(raw)

        with self.assertRaises(SnapshotValidationError):
            install_snapshot(corrupted, target, expected_version="test-build-1")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

        with override_settings(TAVERN_DR_ENCRYPTION_KEY=_b64(b"B" * 32)):
            with self.assertRaises(SnapshotValidationError):
                install_snapshot(package, target, expected_version="test-build-1")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_unaccepted_privacy_notice_blocks_snapshot_creation(self):
        with patch("core.disaster_recovery_snapshot.replication_consent_ready", return_value=False):
            with self.assertRaisesRegex(SnapshotValidationError, "未接受当前灾备隐私告知"):
                self._create()

    def test_incompatible_app_version_does_not_install_snapshot(self):
        package = self._create()
        target = self.root / "replica"
        active = target / "active"
        active.mkdir(parents=True)
        marker = active / "last-good.txt"
        marker.write_text("preserve", encoding="utf-8")

        with self.assertRaises(SnapshotValidationError):
            install_snapshot(package, target, expected_version="different-build")

        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_older_epoch_cannot_roll_back_active_replica(self):
        current = self._create("current.drsnap")
        older_lease = self._signed_lease(epoch=6)
        older = create_snapshot(
            self.root / "older.drsnap",
            older_lease,
            source_db=self.database,
            media_root=self.media,
            freeze_timeout_seconds=2,
        )
        target = self.root / "replica"
        active = install_snapshot(current, target, expected_version="test-build-1")
        before = (active / "manifest.json").read_bytes()

        with self.assertRaises(SnapshotValidationError):
            install_snapshot(older, target, expected_version="test-build-1")

        self.assertEqual((active / "manifest.json").read_bytes(), before)
