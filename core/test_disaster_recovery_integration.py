import base64
import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import SimpleTestCase, override_settings

from .disaster_recovery import Lease, LeaseHeldByAnotherNode, WriteLeaseUnavailable, canonical_lease_payload, require_write_lease, save_local_lease, set_local_role
from .disaster_recovery_failover import poll_once, prepare_failback
from .disaster_recovery_snapshot import SnapshotValidationError, create_snapshot, install_snapshot, validate_installed_replica, validate_snapshot


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _LocalWitness:
    def __init__(self, signing_key):
        self.signing_key = signing_key
        self.lease = None

    def _issue(self, holder_id, epoch):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        unsigned = Lease(holder_id, epoch, now, now + timedelta(seconds=60), "pending")
        return Lease(holder_id, epoch, now, unsigned.expires_at, _b64(self.signing_key.sign(canonical_lease_payload(unsigned))))

    def client(self, node_id):
        witness = self

        class Client:
            def status(self):
                return witness.lease

            def acquire(self):
                now = datetime.now(timezone.utc)
                if witness.lease and witness.lease.expires_at + timedelta(seconds=2) > now:
                    raise LeaseHeldByAnotherNode("lease held")
                epoch = witness.lease.epoch + 1 if witness.lease else 1
                witness.lease = witness._issue(node_id, epoch)
                return witness.lease

            def renew(self, lease):
                if not witness.lease or witness.lease.holder_id != node_id or witness.lease.epoch != lease.epoch:
                    raise LeaseHeldByAnotherNode("lease changed")
                witness.lease = witness._issue(node_id, lease.epoch)
                return witness.lease

            def handoff(self, target_node_id):
                if not witness.lease or witness.lease.holder_id != node_id:
                    raise LeaseHeldByAnotherNode("not holder")
                witness.lease = witness._issue(target_node_id, witness.lease.epoch + 1)
                return witness.lease

        return Client()

    def expire(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        unsigned = Lease(self.lease.holder_id, self.lease.epoch, now - timedelta(seconds=60), now - timedelta(seconds=3), "pending")
        self.lease = Lease(
            unsigned.holder_id,
            unsigned.epoch,
            unsigned.issued_at,
            unsigned.expires_at,
            _b64(self.signing_key.sign(canonical_lease_payload(unsigned))),
        )


class DisasterRecoveryLocalIntegrationTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="neon-dr-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_db = self.root / "154.sqlite3"
        self.source_media = self.root / "154-media"
        self.source_media.mkdir()
        db = sqlite3.connect(self.source_db)
        try:
            db.execute("CREATE TABLE synthetic_records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO synthetic_records(value) VALUES (?)", ("合成账号与对话",))
            db.commit()
        finally:
            db.close()
        (self.source_media / "角色头像.bin").write_bytes(b"synthetic avatar v1")
        self.node_key = Ed25519PrivateKey.generate()
        witness_key = Ed25519PrivateKey.generate()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        unsigned = Lease("154", 11, now, now + timedelta(seconds=60), "pending")
        lease = Lease("154", 11, now, unsigned.expires_at, _b64(witness_key.sign(canonical_lease_payload(unsigned))))
        raw_private = self.node_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        raw_public = self.node_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        settings = override_settings(
            TAVERN_NODE_ID="154",
            TAVERN_APP_VERSION="integration-test",
            TAVERN_DR_ENCRYPTION_KEY=_b64(b"I" * 32),
            TAVERN_DR_ENCRYPTION_KEY_VERSION="integration-key-v1",
            TAVERN_DR_NODE_PRIVATE_KEY=_b64(raw_private),
            TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS=json.dumps({"154": _b64(raw_public)}),
            TAVERN_WITNESS_PUBLIC_KEY=_b64(witness_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )),
            TAVERN_DR_SNAPSHOT_LOCK_PATH=str(self.root / "snapshot.lock"),
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self.lease = lease

    def test_snapshot_transfer_install_and_corruption_preserve_last_good_replica(self):
        package = self.root / "154-to-123.drsnap"
        create_snapshot(
            package,
            self.lease,
            source_db=self.source_db,
            media_root=self.source_media,
        )
        manifest = validate_snapshot(
            package,
            expected_version="integration-test",
            expected_key_version="integration-key-v1",
        )
        replica = self.root / "123-replica"
        active = install_snapshot(
            package,
            replica,
            expected_version="integration-test",
            expected_key_version="integration-key-v1",
        )
        self.assertEqual(
            validate_installed_replica(
                replica,
                expected_version="integration-test",
                expected_key_version="integration-key-v1",
            ).snapshot_id,
            manifest.snapshot_id,
        )

        active_db = active / "database.sqlite3"
        db = sqlite3.connect(active_db)
        try:
            self.assertEqual(db.execute("SELECT value FROM synthetic_records").fetchone()[0], "合成账号与对话")
        finally:
            db.close()
        active_media = active / "media" / "角色头像.bin"
        self.assertEqual(hashlib.sha256(active_media.read_bytes()).hexdigest(), manifest.media_files["角色头像.bin"]["sha256"])

        previous_database_hash = hashlib.sha256(active_db.read_bytes()).hexdigest()
        damaged = self.root / "damaged.drsnap"
        damaged.write_bytes(package.read_bytes()[:-1] + bytes([package.read_bytes()[-1] ^ 0x01]))
        with self.assertRaises(SnapshotValidationError):
            install_snapshot(
                damaged,
                replica,
                expected_version="integration-test",
                expected_key_version="integration-key-v1",
            )
        self.assertEqual(hashlib.sha256(active_db.read_bytes()).hexdigest(), previous_database_hash)

    def test_failover_write_and_controlled_failback_keep_one_writer_and_latest_data(self):
        from django.db.migrations.loader import MigrationLoader

        migrations = sorted(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
        db = sqlite3.connect(self.source_db)
        try:
            db.execute("CREATE TABLE django_migrations (app TEXT NOT NULL, name TEXT NOT NULL, applied TEXT NOT NULL)")
            db.executemany("INSERT INTO django_migrations(app, name, applied) VALUES (?, ?, ?)", [
                (app, name, "2026-09-24 00:00:00") for app, name in migrations
            ])
            db.commit()
        finally:
            db.close()

        standby_root = self.root / "123-replica"
        recovered_root = self.root / "154-replica"
        private_keys = {"154": self.node_key, "123": Ed25519PrivateKey.generate()}
        public_keys = {
            node_id: _b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
            for node_id, key in private_keys.items()
        }
        witness = _LocalWitness(Ed25519PrivateKey.generate())
        witness_public = _b64(witness.signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ))
        common = {
            "TAVERN_DR_ENABLED": False,
            "TAVERN_APP_VERSION": "integration-test",
            "TAVERN_DR_ENCRYPTION_KEY": _b64(b"I" * 32),
            "TAVERN_DR_ENCRYPTION_KEY_VERSION": "integration-key-v1",
            "TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS": json.dumps(public_keys),
            "TAVERN_WITNESS_PUBLIC_KEY": witness_public,
            "TAVERN_DR_SNAPSHOT_LOCK_PATH": str(self.root / "lifecycle.lock"),
            "TAVERN_DR_MAX_SNAPSHOT_BYTES": 10 * 1024 * 1024,
        }

        def node_config(node_id):
            node_root = self.root / node_id
            return {
                **common,
                "TAVERN_NODE_ID": node_id,
                "TAVERN_DR_NODE_PRIVATE_KEY": _b64(private_keys[node_id].private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )),
                "TAVERN_DR_ROLE_PATH": str(node_root / "role.json"),
                "TAVERN_DR_LEASE_PATH": str(node_root / "lease.json"),
                "TAVERN_DR_REPLICA_ROOT": str(standby_root if node_id == "123" else recovered_root),
                "TAVERN_DR_SNAPSHOT_DIR": str(node_root / "snapshots"),
            }

        primary_settings = node_config("154")
        with override_settings(**primary_settings):
            initial_lease = witness.client("154").acquire()
            save_local_lease(initial_lease)
            set_local_role("primary")
            self.assertEqual(require_write_lease().epoch, initial_lease.epoch)
            first_snapshot = self.root / "154-initial.drsnap"
            create_snapshot(first_snapshot, initial_lease, source_db=self.source_db, media_root=self.source_media)
            installed_initial = install_snapshot(
                first_snapshot,
                standby_root,
                expected_version="integration-test",
                expected_key_version="integration-key-v1",
                expected_migrations=tuple(f"{app}:{name}" for app, name in migrations),
            )
            self.assertEqual(validate_installed_replica(
                standby_root,
                expected_version="integration-test",
                expected_key_version="integration-key-v1",
                expected_migrations=tuple(f"{app}:{name}" for app, name in migrations),
            ).epoch, initial_lease.epoch)
            set_local_role("read_only")

        witness.expire()
        standby_settings = node_config("123")
        with override_settings(**standby_settings):
            result = poll_once(witness.client("123"))
            self.assertEqual(result["role"], "primary")
            self.assertGreater(result["epoch"], initial_lease.epoch)
            active_db = installed_initial / "database.sqlite3"
            active_connection = sqlite3.connect(active_db)
            try:
                active_connection.execute("INSERT INTO synthetic_records(value) VALUES (?)", ("123 接管期间的新记录",))
                active_connection.commit()
            finally:
                active_connection.close()
            self.assertEqual(require_write_lease().epoch, result["epoch"])

        with override_settings(**primary_settings):
            result = poll_once(witness.client("154"))
            self.assertEqual(result["role"], "read_only")
            with self.assertRaisesRegex(WriteLeaseUnavailable, "只读"):
                require_write_lease()

        standby_settings.update({
            "MEDIA_ROOT": str(installed_initial / "media"),
            "TAVERN_DR_PEER_URL": "https://local-replica.invalid",
        })

        def local_transfer(package, _destination_url):
            manifest = validate_snapshot(
                package,
                expected_version="integration-test",
                expected_key_version="integration-key-v1",
                expected_migrations=tuple(f"{app}:{name}" for app, name in migrations),
            )
            install_snapshot(
                package,
                recovered_root,
                expected_version="integration-test",
                expected_key_version="integration-key-v1",
                expected_migrations=tuple(f"{app}:{name}" for app, name in migrations),
            )
            return {"status": "replicated", "snapshot_id": manifest.snapshot_id, "epoch": manifest.epoch}

        from django.conf import settings

        with override_settings(**standby_settings), patch.dict(settings.DATABASES["default"], {"NAME": str(active_db)}), patch("core.disaster_recovery_failover.send_snapshot", side_effect=local_transfer):
            handoff = prepare_failback("123", "154", client=witness.client("123"))
            self.assertEqual(handoff["status"], "handed_off")
            self.assertEqual(handoff["new_primary"], "154")

        with override_settings(**primary_settings):
            result = poll_once(witness.client("154"))
            self.assertEqual(result["role"], "primary")
            self.assertEqual(result["epoch"], handoff["epoch"])
            restored_db = sqlite3.connect(recovered_root / "active" / "database.sqlite3")
            try:
                values = [row[0] for row in restored_db.execute("SELECT value FROM synthetic_records ORDER BY id")]
            finally:
                restored_db.close()
            self.assertIn("123 接管期间的新记录", values)
            self.assertEqual(require_write_lease().holder_id, "154")

        with override_settings(**standby_settings):
            result = poll_once(witness.client("123"))
            self.assertEqual(result["role"], "read_only")
            with self.assertRaisesRegex(WriteLeaseUnavailable, "只读"):
                require_write_lease()
