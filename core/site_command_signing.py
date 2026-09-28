import base64
import json
import os
from datetime import timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .site_signing import _PUBLIC_KEY_RE, _SIGNATURE_RE, _decode_base64url, validate_public_key


class SigningKeyUnavailable(ValueError):
    pass


def _encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def canonical_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("命令内容无效")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def load_private_key(env_name):
    path = os.environ.get(env_name, "")
    if not path or not Path(path).is_absolute():
        raise SigningKeyUnavailable("未配置站点凭据")
    resolved = Path(path).resolve()
    if resolved.is_relative_to(settings.BASE_DIR.resolve()):
        raise SigningKeyUnavailable("站点凭据不得位于应用源码内")
    try:
        key = serialization.load_pem_private_key(resolved.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise SigningKeyUnavailable("站点凭据不可用") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyUnavailable("站点凭据类型无效")
    return key


def public_key_from_private_file(env_name="TAVERN_CONTROL_SIGNING_KEY_FILE"):
    key = load_private_key(env_name)
    return _encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def sign_payload(payload):
    return _encode(load_private_key("TAVERN_CONTROL_SIGNING_KEY_FILE").sign(canonical_payload(payload)))


def verify_command(envelope, public_key):
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        return False
    try:
        validate_public_key(public_key)
        signature = _decode_base64url(envelope["signature"], _SIGNATURE_RE, 64)
        key = Ed25519PublicKey.from_public_bytes(_decode_base64url(public_key, _PUBLIC_KEY_RE, 32))
        key.verify(signature, canonical_payload(envelope["payload"]))
    except (ValueError, TypeError, InvalidSignature, OverflowError):
        return False
    return True


def create_signed_command(*, site, actor, action, reason, target_source_user_id="", target_username="", expires_in_seconds=300):
    from .models import FederationAudit, SiteCommand
    import uuid

    # Read the credential before beginning a database transaction.
    load_private_key("TAVERN_CONTROL_SIGNING_KEY_FILE")
    with transaction.atomic():
        command = SiteCommand.objects.create(
            site=site, actor=actor, command_id=str(uuid.uuid4()), action=action,
            target_source_user_id=target_source_user_id, target_username=target_username,
            reason=reason, expires_at=timezone.now() + timedelta(seconds=expires_in_seconds),
        )
        command.signature = sign_payload(command.signed_payload())
        command.save(update_fields=["signature"])
        FederationAudit.objects.create(
            actor=actor, site=site, target_source_user_id=target_source_user_id,
            action=action, reason=reason, metadata={"command_id": command.command_id},
        )
    return command
