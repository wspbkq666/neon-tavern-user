import base64
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .disaster_recovery import Lease, canonical_lease_payload
from .disaster_recovery_snapshot import create_snapshot
from .disaster_recovery_transfer import ReplicationTransferError, send_snapshot, sign_replication_request


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class DisasterRecoveryTransferTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "source.sqlite3"
        database = sqlite3.connect(self.database)
        try:
            database.execute("CREATE TABLE sample (value TEXT)")
            database.execute("INSERT INTO sample VALUES ('transfer fixture')")
            database.commit()
        finally:
            database.close()
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "fixture.txt").write_text("synthetic", encoding="utf-8")
        self.node_key = Ed25519PrivateKey.generate()
        self.witness_key = Ed25519PrivateKey.generate()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        unsigned = Lease("154", 1, self.now, self.now + timedelta(seconds=60), "pending")
        lease = Lease("154", 1, unsigned.issued_at, unsigned.expires_at, _b64(self.witness_key.sign(canonical_lease_payload(unsigned))))
        self.private = _b64(self.node_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
        self.public = _b64(self.node_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.key = _b64(b"K" * 32)
        with override_settings(
            TAVERN_NODE_ID="154",
            TAVERN_APP_VERSION="transfer-test",
            TAVERN_DR_NODE_PRIVATE_KEY=self.private,
            TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS=json.dumps({"154": self.public}),
            TAVERN_DR_ENCRYPTION_KEY=self.key,
            TAVERN_DR_ENCRYPTION_KEY_VERSION="test-v1",
            TAVERN_DR_SNAPSHOT_LOCK_PATH=str(self.root / "snapshot.lock"),
        ):
            self.package = create_snapshot(
                self.root / "replica.drsnap", lease, source_db=self.database,
                media_root=self.media, freeze_timeout_seconds=2,
            )
        self.content = self.package.read_bytes()

    def _post(self, *, signature=None):
        path = "/api/disaster-recovery/replica/"
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        nonce = "request-1234567890"
        headers = sign_replication_request(
            self.node_key, "154", "POST", path, timestamp, nonce, self.content
        )
        if signature is not None:
            headers["X-DR-Signature"] = signature
        return self.client.post(
            path,
            {"snapshot": SimpleUploadedFile("replica.drsnap", self.content, content_type="application/octet-stream")},
            **{f"HTTP_{key.upper().replace('-', '_')}": value for key, value in headers.items()},
        )

    def test_http_peer_is_rejected_by_default(self):
        with override_settings(TAVERN_DR_NODE_PRIVATE_KEY=self.private, TAVERN_NODE_ID="154"):
            with self.assertRaises(ReplicationTransferError):
                send_snapshot(self.package, "http://154.222.26.47:8088")

    def test_explicit_personal_http_peer_requires_allowlisted_host_without_disabling_site_tls(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "replicated", "snapshot_id": "fixture", "created_at": "now"})

        with override_settings(
            INSECURE_HTTP=False,
            TAVERN_DR_ALLOW_HTTP_PEER=True,
            TAVERN_DR_HTTP_PEER_HOSTS=["154.222.26.47"],
            TAVERN_DR_NODE_PRIVATE_KEY=self.private,
            TAVERN_NODE_ID="123",
            TAVERN_DR_STATUS_PATH=self.root / "replication-status.json",
        ):
            result = send_snapshot(
                self.package,
                "http://154.222.26.47:8088",
                transport=httpx.MockTransport(respond),
            )

        self.assertEqual(result["status"], "replicated")
        self.assertEqual(calls[0].url.scheme, "http")
        self.assertTrue(calls[0].headers.get("X-DR-Signature"))

    def test_personal_http_peer_rejects_unlisted_host(self):
        with override_settings(
            INSECURE_HTTP=False,
            TAVERN_DR_ALLOW_HTTP_PEER=True,
            TAVERN_DR_HTTP_PEER_HOSTS=["154.222.26.47"],
            TAVERN_DR_NODE_PRIVATE_KEY=self.private,
            TAVERN_NODE_ID="123",
        ):
            with self.assertRaises(ReplicationTransferError):
                send_snapshot(self.package, "http://example.com:8088")

    def test_read_only_replica_accepts_only_signed_verified_snapshot(self):
        with override_settings(
            TAVERN_DR_ENABLED=True,
            TAVERN_DR_READ_ONLY=True,
            TAVERN_NODE_ID="123",
            TAVERN_APP_VERSION="transfer-test",
            TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS=json.dumps({"154": self.public}),
            TAVERN_DR_ENCRYPTION_KEY=self.key,
            TAVERN_DR_ENCRYPTION_KEY_VERSION="test-v1",
            TAVERN_DR_REPLICA_ROOT=self.root / "receiver",
            TAVERN_DR_STATUS_PATH=self.root / "status.json",
            TAVERN_DR_ROLE_PATH=self.root / "role.json",
        ):
            response = self._post()

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads((self.root / "status.json").read_text())["status"], "healthy")
        active = self.root / "receiver" / "active"
        self.assertTrue((active / "media" / "fixture.txt").is_file())
        database = sqlite3.connect(active / "database.sqlite3")
        try:
            self.assertEqual(database.execute("SELECT value FROM sample").fetchone()[0], "transfer fixture")
        finally:
            database.close()

    def test_signed_replica_upload_does_not_require_browser_csrf_token(self):
        self.client = Client(enforce_csrf_checks=True)
        with override_settings(
            TAVERN_DR_ENABLED=True,
            TAVERN_DR_READ_ONLY=True,
            TAVERN_NODE_ID="123",
            TAVERN_APP_VERSION="transfer-test",
            TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS=json.dumps({"154": self.public}),
            TAVERN_DR_ENCRYPTION_KEY=self.key,
            TAVERN_DR_ENCRYPTION_KEY_VERSION="test-v1",
            TAVERN_DR_REPLICA_ROOT=self.root / "receiver",
            TAVERN_DR_STATUS_PATH=self.root / "status.json",
            TAVERN_DR_ROLE_PATH=self.root / "role.json",
        ):
            response = self._post()

        self.assertEqual(response.status_code, 200, response.content)

    def test_read_only_replica_rejects_invalid_signature(self):
        with override_settings(
            TAVERN_DR_ENABLED=True,
            TAVERN_DR_READ_ONLY=True,
            TAVERN_NODE_ID="123",
            TAVERN_APP_VERSION="transfer-test",
            TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS=json.dumps({"154": self.public}),
            TAVERN_DR_ENCRYPTION_KEY=self.key,
            TAVERN_DR_ENCRYPTION_KEY_VERSION="test-v1",
            TAVERN_DR_REPLICA_ROOT=self.root / "receiver",
            TAVERN_DR_STATUS_PATH=self.root / "status.json",
            TAVERN_DR_ROLE_PATH=self.root / "role.json",
        ):
            response = self._post(signature=_b64(b"X" * 64))

        self.assertEqual(response.status_code, 401)
        self.assertFalse((self.root / "receiver" / "active").exists())
