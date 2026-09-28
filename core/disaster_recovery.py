import base64
import binascii
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.db import DatabaseError


class WitnessUnavailable(RuntimeError):
    pass


class WitnessProtocolError(RuntimeError):
    pass


class LeaseHeldByAnotherNode(RuntimeError):
    pass


class WriteLeaseUnavailable(DatabaseError):
    pass


@dataclass(frozen=True)
class Lease:
    holder_id: str
    epoch: int
    issued_at: datetime
    expires_at: datetime
    signature: str

    def __post_init__(self):
        if not self.holder_id or self.epoch < 1:
            raise ValueError("租约持有者和任期无效")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("租约时间必须包含时区")
        if self.expires_at <= self.issued_at:
            raise ValueError("租约到期时间必须晚于签发时间")


def canonical_lease_payload(lease: Lease) -> bytes:
    value = {
        "epoch": lease.epoch,
        "expires_at": int(lease.expires_at.timestamp()),
        "holder_id": lease.holder_id,
        "issued_at": int(lease.issued_at.timestamp()),
    }
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def lease_to_dict(lease: Lease) -> dict:
    return {
        "holder_id": lease.holder_id,
        "epoch": lease.epoch,
        "issued_at": int(lease.issued_at.timestamp()),
        "expires_at": int(lease.expires_at.timestamp()),
        "signature": lease.signature,
    }


def lease_from_dict(value: dict) -> Lease:
    try:
        return Lease(
            holder_id=value["holder_id"],
            epoch=int(value["epoch"]),
            issued_at=datetime.fromtimestamp(int(value["issued_at"]), tz=timezone.utc),
            expires_at=datetime.fromtimestamp(int(value["expires_at"]), tz=timezone.utc),
            signature=value["signature"],
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise WitnessProtocolError("仲裁器返回的租约格式无效") from exc


def _decode_signature(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("签名无效")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("签名无效") from exc
    if len(decoded) != 64 or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise ValueError("签名无效")
    return decoded


def verify_lease(lease: Lease, public_key: Ed25519PublicKey, now: datetime) -> bool:
    if not isinstance(public_key, Ed25519PublicKey) or now.tzinfo is None:
        return False
    if lease.issued_at.timestamp() > now.timestamp() + 2:
        return False
    try:
        public_key.verify(_decode_signature(lease.signature), canonical_lease_payload(lease))
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def lease_write_allowed(
    lease: Lease,
    node_id: str,
    now: datetime,
    max_clock_skew_seconds: int = 2,
    min_remaining_seconds: int = 10,
) -> bool:
    if now.tzinfo is None or lease.holder_id != node_id:
        return False
    current = now.timestamp()
    return (
        lease.issued_at.timestamp() <= current + max_clock_skew_seconds
        and lease.expires_at.timestamp() > current
        and lease.expires_at.timestamp() - current >= min_remaining_seconds
    )


def get_local_lease() -> Lease | None:
    path = getattr(settings, "TAVERN_DR_LEASE_PATH", "")
    encoded_key = getattr(settings, "TAVERN_WITNESS_PUBLIC_KEY", "")
    if not path or not encoded_key:
        return None
    try:
        raw_key = base64.b64decode(encoded_key + "=" * (-len(encoded_key) % 4), altchars=b"-_", validate=True)
        if len(raw_key) != 32:
            return None
        public_key = Ed25519PublicKey.from_public_bytes(raw_key)
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        lease = lease_from_dict(value.get("lease", value))
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError, binascii.Error):
        return None
    if not verify_lease(lease, public_key, datetime.now(timezone.utc)):
        return None
    return lease


def save_local_lease(lease: Lease) -> None:
    path_value = getattr(settings, "TAVERN_DR_LEASE_PATH", "")
    if not path_value:
        raise WriteLeaseUnavailable("本地写租约缓存路径未配置")
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as target:
            json.dump({"lease": lease_to_dict(lease)}, target, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def is_read_only_node() -> bool:
    return local_role_state()["role"] == "read_only"


def local_role_state() -> dict:
    role_path = getattr(settings, "TAVERN_DR_ROLE_PATH", "")
    if role_path:
        try:
            value = json.loads(Path(role_path).read_text(encoding="utf-8"))
            role = value.get("role")
            if role in {"primary", "read_only"}:
                return {"role": role, "generation": max(0, int(value.get("generation", 0)))}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return {"role": "read_only" if getattr(settings, "TAVERN_DR_READ_ONLY", False) else "primary", "generation": 0}


def set_local_role(role: str) -> None:
    if role not in {"primary", "read_only"}:
        raise ValueError("节点角色无效")
    path = Path(settings.TAVERN_DR_ROLE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = local_role_state()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as target:
            json.dump({
                "role": role,
                "generation": current["generation"] + 1,
                "updated_at": int(time.time()),
            }, target, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def require_write_lease(*, min_remaining_seconds: int = 10) -> Lease:
    if is_read_only_node():
        raise WriteLeaseUnavailable("备用节点处于只读状态")
    lease = get_local_lease()
    node_id = getattr(settings, "TAVERN_NODE_ID", "")
    if lease is None or not lease_write_allowed(
        lease,
        node_id,
        datetime.now(timezone.utc),
        min_remaining_seconds=min_remaining_seconds,
    ):
        raise WriteLeaseUnavailable("当前节点没有足够时长的有效写租约")
    return lease


def replication_consent_ready() -> bool:
    if not getattr(settings, "TAVERN_DR_ENABLED", False):
        return True
    from django.contrib.auth import get_user_model
    from .auth_api import PRIVACY_POLICY_VERSION, USAGE_RULES_VERSION
    from .models import UserProfile

    total_users = get_user_model().objects.count()
    accepted = UserProfile.objects.filter(
        policy_consent_at__isnull=False,
        privacy_policy_version=PRIVACY_POLICY_VERSION,
        usage_rules_version=USAGE_RULES_VERSION,
    ).count()
    return accepted == total_users


def assert_lease_valid_at_commit(lease: Lease, *, transaction_started_at: float | None = None) -> None:
    current = require_write_lease(min_remaining_seconds=0)
    if current.holder_id != lease.holder_id or current.epoch != lease.epoch:
        raise WriteLeaseUnavailable("写租约任期已变化，事务已回滚")
    if transaction_started_at is not None and time.monotonic() - transaction_started_at > 10:
        raise WriteLeaseUnavailable("写事务超过 10 秒上限，事务已回滚")


def canonical_request_message(node_id: str, method: str, path: str, timestamp: str, request_id: str, body: bytes) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    return f"{node_id}\n{method.upper()}\n{path}\n{timestamp}\n{request_id}\n{body_digest}".encode("ascii")


class WitnessClient:
    def __init__(
        self,
        base_url: str,
        node_id: str,
        private_key: Ed25519PrivateKey,
        witness_public_key: Ed25519PublicKey,
        *,
        timeout: float = 3,
        transport=None,
        ca_bundle: str | Path | None = None,
    ):
        parsed_url = urlsplit(base_url)
        local_http = parsed_url.scheme == "http" and parsed_url.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed_url.scheme != "https" and not local_http:
            raise ValueError("仲裁器地址必须使用 HTTPS；仅允许本机隔离测试使用 HTTP")
        self.base_url = base_url.rstrip("/")
        self.node_id = node_id
        self.private_key = private_key
        self.witness_public_key = witness_public_key
        self.timeout = timeout
        self.transport = transport
        self.ca_bundle = str(ca_bundle) if ca_bundle else None

    def acquire(self, *, now: datetime | None = None) -> Lease:
        return self._signed_post("/api/lease/acquire/", now=now)

    def renew(self, lease: Lease, *, now: datetime | None = None) -> Lease:
        return self._signed_post("/api/lease/renew/", {"epoch": lease.epoch}, now=now)

    def handoff(self, target_node_id: str, *, now: datetime | None = None) -> Lease:
        return self._signed_post("/api/lease/handoff/", {"target_node_id": target_node_id}, now=now, allow_other_holder=True)

    def status(self) -> Lease | None:
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                verify=self.ca_bundle or True,
            ) as client:
                response = client.get(f"{self.base_url}/api/lease/status/")
        except (httpx.HTTPError, OSError) as exc:
            raise WitnessUnavailable("无法连接仲裁器") from exc
        if response.status_code != 200:
            raise WitnessUnavailable(f"仲裁器状态查询失败（HTTP {response.status_code}）")
        try:
            value = response.json().get("lease")
            if value is None:
                return None
            lease = lease_from_dict(value)
        except (AttributeError, ValueError, TypeError, WitnessProtocolError) as exc:
            raise WitnessProtocolError("仲裁器状态响应无效") from exc
        if not verify_lease(lease, self.witness_public_key, datetime.now(timezone.utc)):
            raise WitnessProtocolError("仲裁器状态签名无效或租约已失效")
        return lease

    def _signed_post(self, path: str, data: dict | None = None, *, now: datetime | None = None, allow_other_holder=False) -> Lease:
        now = now or datetime.now(timezone.utc)
        timestamp = str(int(now.timestamp()))
        request_id = uuid.uuid4().hex
        body = json.dumps(data or {}, sort_keys=True, separators=(",", ":")).encode("ascii")
        signature = base64.urlsafe_b64encode(
            self.private_key.sign(canonical_request_message(self.node_id, "POST", path, timestamp, request_id, body))
        ).decode("ascii").rstrip("=")
        headers = {
            "Content-Type": "application/json",
            "X-Witness-Node": self.node_id,
            "X-Witness-Time": timestamp,
            "X-Witness-Request-Id": request_id,
            "X-Witness-Signature": signature,
        }
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                verify=self.ca_bundle or True,
            ) as client:
                response = client.post(f"{self.base_url}{path}", content=body, headers=headers)
        except (httpx.HTTPError, OSError) as exc:
            raise WitnessUnavailable("无法连接仲裁器") from exc
        if response.status_code == 409:
            try:
                error_code = response.json().get("error_code")
            except (AttributeError, ValueError):
                error_code = ""
            if error_code in {"lease_held", "initial_node_required"}:
                raise LeaseHeldByAnotherNode("当前仍有其他节点持有写租约")
            raise WitnessProtocolError("仲裁器拒绝重复或无效的租约请求")
        if response.status_code != 200:
            raise WitnessProtocolError(f"仲裁器拒绝租约请求（HTTP {response.status_code}）")
        try:
            lease = lease_from_dict(response.json()["lease"])
        except (KeyError, ValueError, TypeError, WitnessProtocolError) as exc:
            raise WitnessProtocolError("仲裁器租约响应无效") from exc
        if not verify_lease(lease, self.witness_public_key, datetime.now(timezone.utc)):
            raise WitnessProtocolError("仲裁器签发的租约签名无效")
        if lease.holder_id != self.node_id and not allow_other_holder:
            raise WitnessProtocolError("仲裁器签发给了其他节点")
        return lease
