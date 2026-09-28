import base64
import hashlib
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import MarketNonce, MarketSiteCredential, SiteSettings
from .settings_api import decrypt_key, encrypt_key


MAX_CLOCK_SKEW = 300
NONCE_SECONDS = 600
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def generate_site_keypair():
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_pem, base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")


def encode_public_key(public_key):
    try:
        raw = base64.urlsafe_b64decode(public_key + "=" * (-len(public_key) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError("站点公钥格式无效") from exc
    if len(raw) != 32:
        raise ValueError("站点公钥格式无效")
    return raw


def canonical_message(method, path, timestamp, nonce, body):
    digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode("ascii")


def signed_headers(site_id, private_pem, method, path, body=b""):
    private_key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("站点私钥格式无效")
    timestamp = str(int(time.time()))
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode("ascii").rstrip("=")
    message = canonical_message(method, path, timestamp, nonce, body)
    signature = base64.urlsafe_b64encode(private_key.sign(message)).decode("ascii").rstrip("=")
    return {
        "X-Market-Site": str(site_id),
        "X-Market-Time": timestamp,
        "X-Market-Nonce": nonce,
        "X-Market-Signature": signature,
    }


def site_private_key(record):
    if not record.encrypted_market_private_key:
        return None
    return decrypt_key(record.encrypted_market_private_key)


def create_and_store_site_keypair(record):
    private_pem, public_key = generate_site_keypair()
    record.encrypted_market_private_key = encrypt_key(private_pem)
    record.save(update_fields=["encrypted_market_private_key", "updated_at"])
    return public_key


def verify_signed_request(request):
    site_id = request.headers.get("X-Market-Site", "")
    timestamp = request.headers.get("X-Market-Time", "")
    nonce = request.headers.get("X-Market-Nonce", "")
    signature = request.headers.get("X-Market-Signature", "")
    if not site_id or not timestamp or not _NONCE_RE.fullmatch(nonce):
        return None, "跨站签名信息不完整"
    try:
        timestamp_value = int(timestamp)
        site = MarketSiteCredential.objects.get(site_id=site_id, status=MarketSiteCredential.APPROVED)
        public_key = Ed25519PublicKey.from_public_bytes(encode_public_key(site.public_key))
        signature_bytes = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        path = urlsplit(request.get_full_path()).path
        public_key.verify(signature_bytes, canonical_message(request.method, path, timestamp, nonce, request.body))
    except (ValueError, TypeError, MarketSiteCredential.DoesNotExist, InvalidSignature):
        return None, "公共市场站点未批准或签名无效"
    if abs(int(time.time()) - timestamp_value) > MAX_CLOCK_SKEW:
        return None, "跨站签名已过期"

    cutoff = timezone.now() - timedelta(seconds=NONCE_SECONDS)
    MarketNonce.objects.filter(created_at__lt=cutoff).delete()
    try:
        with transaction.atomic():
            MarketNonce.objects.create(site=site, value=nonce)
    except IntegrityError:
        return None, "跨站请求已处理，请重新尝试"
    recent_count = MarketNonce.objects.filter(site=site, created_at__gte=timezone.now() - timedelta(minutes=1)).count()
    if recent_count > 120:
        return None, "公共市场请求过于频繁，请稍后重试"
    return site, None


def public_key_for_site(record):
    private_pem = site_private_key(record)
    if not private_pem:
        return ""
    private_key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def site_market_configuration():
    return SiteSettings.objects.get_or_create(pk=1)[0]
