import base64
import binascii
import hashlib
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9_-]{86}$")
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _decode_base64url(value, pattern, expected_length):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("密钥或签名格式无效")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("密钥或签名格式无效") from exc
    if len(raw) != expected_length or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise ValueError("密钥或签名格式无效")
    return raw


def validate_public_key(value):
    Ed25519PublicKey.from_public_bytes(_decode_base64url(value, _PUBLIC_KEY_RE, 32))
    return value


def canonical_message(site_id, method, path, timestamp, nonce, body):
    digest = hashlib.sha256(body).hexdigest()
    return f"{site_id}\n{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode("ascii")
