import base64
import binascii
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

from core.disaster_recovery import Lease, canonical_lease_payload


def _decode_key(value, expected_length):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("密钥配置无效")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("密钥配置无效") from exc
    if len(decoded) != expected_length or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise ValueError("密钥配置无效")
    return decoded


def load_node_public_key(node_id):
    try:
        encoded = json.loads(settings.WITNESS_NODE_PUBLIC_KEYS)[node_id]
        return Ed25519PublicKey.from_public_bytes(_decode_key(encoded, 32))
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("节点身份无效") from exc


def load_witness_private_key():
    return Ed25519PrivateKey.from_private_bytes(_decode_key(settings.WITNESS_SIGNING_PRIVATE_KEY, 32))


def encode_signature(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_signature(value):
    return _decode_key(value, 64)


def verify_node_signature(node_id, signature, message):
    try:
        load_node_public_key(node_id).verify(decode_signature(signature), message)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def sign_lease(lease: Lease) -> Lease:
    signature = encode_signature(load_witness_private_key().sign(canonical_lease_payload(lease)))
    return Lease(
        holder_id=lease.holder_id,
        epoch=lease.epoch,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        signature=signature,
    )


def witness_public_key_bytes():
    raw = load_witness_private_key().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return encode_signature(raw)
