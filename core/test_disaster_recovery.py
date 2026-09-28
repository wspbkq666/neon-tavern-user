import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import SimpleTestCase

from .disaster_recovery import Lease, WitnessClient, WitnessUnavailable, lease_to_dict, lease_write_allowed, verify_lease


def _signed_lease(private_key, *, holder_id="154", epoch=1, issued_at=None, expires_at=None):
    issued_at = issued_at or datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc)
    expires_at = expires_at or issued_at + timedelta(seconds=60)
    payload = {
        "epoch": epoch,
        "expires_at": int(expires_at.timestamp()),
        "holder_id": holder_id,
        "issued_at": int(issued_at.timestamp()),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    signature = base64.urlsafe_b64encode(private_key.sign(canonical)).decode("ascii").rstrip("=")
    return Lease(
        holder_id=holder_id,
        epoch=epoch,
        issued_at=issued_at,
        expires_at=expires_at,
        signature=signature,
    )


class DisasterRecoveryLeaseTests(SimpleTestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.now = datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc)
        self.lease = _signed_lease(self.private_key)

    def test_accepts_current_signed_lease_for_its_holder(self):
        self.assertTrue(verify_lease(self.lease, self.public_key, self.now))
        self.assertTrue(lease_write_allowed(self.lease, "154", self.now))

    def test_rejects_lease_for_another_node_or_after_expiration(self):
        self.assertFalse(lease_write_allowed(self.lease, "123", self.now))
        expired_at = self.lease.expires_at + timedelta(microseconds=1)
        self.assertFalse(lease_write_allowed(self.lease, "154", expired_at))

    def test_expired_lease_remains_verifiable_as_authoritative_history_but_not_writable(self):
        expired_at = self.lease.expires_at + timedelta(seconds=1)

        self.assertTrue(verify_lease(self.lease, self.public_key, expired_at))
        self.assertFalse(lease_write_allowed(self.lease, "154", expired_at))

    def test_refuses_to_start_write_when_less_than_ten_seconds_remain(self):
        near_expiry = self.lease.expires_at - timedelta(seconds=5)

        self.assertFalse(lease_write_allowed(self.lease, "154", near_expiry))

    def test_does_not_treat_exact_expiration_as_writable_even_with_zero_grace(self):
        self.assertFalse(lease_write_allowed(self.lease, "154", self.lease.expires_at, min_remaining_seconds=0))

    def test_rejects_tampered_epoch_and_invalid_signature(self):
        tampered = Lease(
            holder_id=self.lease.holder_id,
            epoch=self.lease.epoch + 1,
            issued_at=self.lease.issued_at,
            expires_at=self.lease.expires_at,
            signature=self.lease.signature,
        )
        self.assertFalse(verify_lease(tampered, self.public_key, self.now))
        self.assertFalse(verify_lease(self.lease, Ed25519PrivateKey.generate().public_key(), self.now))

    def test_witness_status_and_signed_requests_use_dedicated_ca_bundle(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        lease = _signed_lease(
            self.private_key,
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        response = Mock(status_code=200)
        response.json.return_value = {"lease": lease_to_dict(lease)}
        http_client = Mock()
        http_client.__enter__ = Mock(return_value=http_client)
        http_client.__exit__ = Mock(return_value=False)
        http_client.get.return_value = response
        http_client.post.return_value = response

        with patch("core.disaster_recovery.httpx.Client", return_value=http_client) as client_factory:
            client = WitnessClient(
                "https://witness.example:43111",
                "154",
                self.private_key,
                self.public_key,
                ca_bundle="/etc/neon-tavern/witness-ca.pem",
            )
            self.assertEqual(client.status(), lease)
            client.acquire()

        self.assertEqual(client_factory.call_count, 2)
        self.assertEqual(
            [call.kwargs["verify"] for call in client_factory.call_args_list],
            ["/etc/neon-tavern/witness-ca.pem"] * 2,
        )

    def test_witness_client_keeps_system_certificate_verification_by_default(self):
        response = Mock(status_code=200)
        response.json.return_value = {"lease": None}
        http_client = Mock()
        http_client.__enter__ = Mock(return_value=http_client)
        http_client.__exit__ = Mock(return_value=False)
        http_client.get.return_value = response

        with patch("core.disaster_recovery.httpx.Client", return_value=http_client) as client_factory:
            client = WitnessClient(
                "https://witness.example",
                "154",
                self.private_key,
                self.public_key,
            )
            self.assertIsNone(client.status())

        self.assertIs(client_factory.call_args.kwargs["verify"], True)

    def test_missing_ca_bundle_fails_as_witness_unavailable(self):
        client = WitnessClient(
            "https://witness.example",
            "154",
            self.private_key,
            self.public_key,
            ca_bundle="C:/missing/witness-ca.pem",
        )

        with patch("core.disaster_recovery.httpx.Client", side_effect=OSError("CA bundle unavailable")):
            with self.assertRaises(WitnessUnavailable):
                client.status()
