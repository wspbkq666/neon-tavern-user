import base64
import binascii
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

from .disaster_recovery import Lease, canonical_lease_payload, lease_write_allowed, replication_consent_ready


_MAGIC = b"NTDR\x01"
_AAD = b"neon-tavern-disaster-recovery-v1"
_CHUNK_SIZE = 1024 * 1024


class SnapshotValidationError(RuntimeError):
    pass


class SnapshotLockTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotManifest:
    snapshot_id: str
    source_node_id: str
    epoch: int
    app_version: str
    migration_versions: tuple[str, ...]
    created_at: str
    database_sha256: str
    media_files: dict[str, dict]
    encryption_key_version: str
    signature: str

    @classmethod
    def from_dict(cls, value):
        try:
            return cls(
                snapshot_id=str(value["snapshot_id"]),
                source_node_id=str(value["source_node_id"]),
                epoch=int(value["epoch"]),
                app_version=str(value["app_version"]),
                migration_versions=tuple(value["migration_versions"]),
                created_at=str(value["created_at"]),
                database_sha256=str(value["database_sha256"]),
                media_files=value["media_files"],
                encryption_key_version=str(value["encryption_key_version"]),
                signature=str(value["signature"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotValidationError("快照清单格式无效") from exc


def _decode_key(value, expected_length, label):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise SnapshotValidationError(f"{label}配置无效")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SnapshotValidationError(f"{label}配置无效") from exc
    if len(raw) != expected_length or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise SnapshotValidationError(f"{label}配置无效")
    return raw


def _encryption_key():
    return _decode_key(settings.TAVERN_DR_ENCRYPTION_KEY, 32, "复制加密密钥")


def _node_private_key():
    return Ed25519PrivateKey.from_private_bytes(
        _decode_key(settings.TAVERN_DR_NODE_PRIVATE_KEY, 32, "节点签名私钥")
    )


def _trusted_public_key(node_id):
    try:
        encoded = json.loads(settings.TAVERN_DR_TRUSTED_NODE_PUBLIC_KEYS)[node_id]
        return Ed25519PublicKey.from_public_bytes(_decode_key(encoded, 32, "节点签名公钥"))
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError("快照来源节点不受信任") from exc


def _manifest_unsigned(manifest):
    payload = asdict(manifest)
    payload.pop("signature")
    payload["migration_versions"] = list(manifest.migration_versions)
    return payload


def _canonical_manifest(manifest):
    return json.dumps(_manifest_unsigned(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign_manifest(manifest):
    signature = base64.urlsafe_b64encode(_node_private_key().sign(_canonical_manifest(manifest))).decode("ascii").rstrip("=")
    return SnapshotManifest(**{**asdict(manifest), "signature": signature})


def _verify_manifest_signature(manifest):
    try:
        signature = _decode_key(manifest.signature, 64, "快照签名")
        _trusted_public_key(manifest.source_node_id).verify(signature, _canonical_manifest(manifest))
    except (InvalidSignature, ValueError) as exc:
        raise SnapshotValidationError("快照来源签名无效") from exc


def _migrations(database_path):
    database = sqlite3.connect(database_path)
    try:
        exists = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='django_migrations'"
        ).fetchone()
        if not exists:
            return ()
        return tuple(
            f"{app}:{name}"
            for app, name in database.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        )
    finally:
        database.close()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_write_lock(lock_path, timeout_seconds=10):
    return _SnapshotFileLock(Path(lock_path), timeout_seconds)


class _SnapshotFileLock:
    def __init__(self, path, timeout_seconds):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.file = None
        self.locked = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if os.name == "nt":
            import msvcrt

            if self.path.stat().st_size == 0:
                self.file.seek(0)
                self.file.write(b"\0")
                self.file.flush()
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                try:
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                    self.locked = True
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        self.file.close()
                        raise SnapshotLockTimeout("等待写入排空超时")
                    time.sleep(0.05)
        import fcntl

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.locked = True
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.file.close()
                    raise SnapshotLockTimeout("等待写入排空超时")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if self.file is None:
            return
        try:
            if self.locked:
                if os.name == "nt":
                    import msvcrt

                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None
            self.locked = False


def _copy_media(source_root, target_root):
    source_root = Path(source_root)
    target_root = Path(target_root)
    manifest = {}
    if not source_root.exists():
        return manifest
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        if path.is_symlink():
            raise SnapshotValidationError("媒体目录包含符号链接，已拒绝快照")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SnapshotValidationError("媒体目录包含不支持的文件")
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        manifest[relative.as_posix()] = {"size": destination.stat().st_size, "sha256": _sha256(destination)}
    return manifest


def _create_sqlite_backup(source_path, destination_path):
    source_path = Path(source_path)
    if not source_path.is_file():
        raise SnapshotValidationError("源站 SQLite 数据库不存在")
    source = sqlite3.connect(str(source_path), timeout=20)
    target = sqlite3.connect(str(destination_path))
    try:
        source.backup(target, pages=256, sleep=0.05)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise SnapshotValidationError("源站 SQLite 一致性检查失败")
        target.commit()
    finally:
        target.close()
        source.close()


def _write_archive(work_root, manifest, output_path):
    manifest_path = work_root / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    archive_path = work_root / "snapshot.tar"
    with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.add(manifest_path, arcname="manifest.json", recursive=False)
        archive.add(work_root / "database.sqlite3", arcname="database.sqlite3", recursive=False)
        media_root = work_root / "media"
        if media_root.exists():
            for path in sorted(media_root.rglob("*")):
                if path.is_file():
                    archive.add(path, arcname=f"media/{path.relative_to(media_root).as_posix()}", recursive=False)
    nonce = os.urandom(12)
    temporary_output = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.partial")
    try:
        encryptor = Cipher(algorithms.AES(_encryption_key()), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(_AAD)
        with archive_path.open("rb") as source, temporary_output.open("wb") as target:
            target.write(_MAGIC + nonce)
            for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                target.write(encryptor.update(chunk))
            target.write(encryptor.finalize())
            target.write(encryptor.tag)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_output, output_path)
    finally:
        temporary_output.unlink(missing_ok=True)


def create_snapshot(
    destination: Path,
    lease: Lease,
    *,
    source_db: Path | None = None,
    media_root: Path | None = None,
    freeze_timeout_seconds: int = 10,
) -> Path:
    now = datetime.now(timezone.utc)
    if lease.holder_id != settings.TAVERN_NODE_ID or not lease_write_allowed(
        lease, settings.TAVERN_NODE_ID, now, min_remaining_seconds=10
    ):
        raise SnapshotValidationError("只有持有有效写租约的主站可以生成快照")
    source_db = Path(source_db or settings.DATABASES["default"]["NAME"])
    media_root = Path(media_root or settings.MEDIA_ROOT)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(settings.TAVERN_DR_SNAPSHOT_LOCK_PATH)
    try:
        with snapshot_write_lock(lock_path, freeze_timeout_seconds):
            if not replication_consent_ready():
                raise SnapshotValidationError("尚有账号未接受当前灾备隐私告知，已阻止复制")
            with tempfile.TemporaryDirectory(prefix="neon-dr-create-", dir=destination.parent) as temporary:
                work_root = Path(temporary)
                _create_sqlite_backup(source_db, work_root / "database.sqlite3")
                media_manifest = _copy_media(media_root, work_root / "media")
                if not lease_write_allowed(lease, settings.TAVERN_NODE_ID, datetime.now(timezone.utc), min_remaining_seconds=0):
                    raise SnapshotValidationError("生成快照期间写租约失效，已取消本次复制")
                manifest = SnapshotManifest(
                    snapshot_id=uuid.uuid4().hex,
                    source_node_id=settings.TAVERN_NODE_ID,
                    epoch=lease.epoch,
                    app_version=settings.TAVERN_APP_VERSION,
                    migration_versions=_migrations(work_root / "database.sqlite3"),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    database_sha256=_sha256(work_root / "database.sqlite3"),
                    media_files=media_manifest,
                    encryption_key_version=settings.TAVERN_DR_ENCRYPTION_KEY_VERSION,
                    signature="pending",
                )
                manifest = _sign_manifest(manifest)
                _write_archive(work_root, manifest, destination)
    except SnapshotLockTimeout as exc:
        raise SnapshotValidationError("等待在途写入排空超时，未生成快照") from exc
    return destination


def _safe_member_path(value):
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _decrypt_to_directory(package: Path, work_root: Path, *, expected_version=None, expected_key_version=None, expected_migrations=None):
    archive_path = work_root / "snapshot.tar"
    try:
        package = Path(package)
        package_size = package.stat().st_size
        maximum = int(getattr(settings, "TAVERN_DR_MAX_SNAPSHOT_BYTES", 2 * 1024 * 1024 * 1024))
        if package_size < len(_MAGIC) + 12 + 16 or package_size > maximum + len(_MAGIC) + 12 + 16:
            raise SnapshotValidationError("快照文件大小无效")
        with package.open("rb") as source:
            header = source.read(len(_MAGIC) + 12)
            if len(header) != len(_MAGIC) + 12 or not header.startswith(_MAGIC):
                raise SnapshotValidationError("快照文件头无效")
            nonce = header[len(_MAGIC):]
            source.seek(-16, os.SEEK_END)
            tag = source.read(16)
            remaining = package_size - len(header) - 16
            source.seek(len(header))
            decryptor = Cipher(algorithms.AES(_encryption_key()), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(_AAD)
            with archive_path.open("wb") as clear:
                while remaining:
                    chunk = source.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise SnapshotValidationError("快照内容意外截断")
                    remaining -= len(chunk)
                    clear.write(decryptor.update(chunk))
                clear.write(decryptor.finalize())
    except (OSError, InvalidTag, ValueError) as exc:
        if isinstance(exc, SnapshotValidationError):
            raise
        raise SnapshotValidationError("快照无法解密或文件已损坏") from exc

    extracted_names = set()
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive.getmembers():
                if not _safe_member_path(member.name) or not member.isfile() or member.name in extracted_names:
                    raise SnapshotValidationError("快照包含不安全或重复的文件路径")
                extracted_names.add(member.name)
                if member.name not in {"manifest.json", "database.sqlite3"} and not member.name.startswith("media/"):
                    raise SnapshotValidationError("快照包含未声明的文件")
                if member.size > 8 * 1024 * 1024 * 1024:
                    raise SnapshotValidationError("快照内单个文件超过限制")
                if member.name == "manifest.json":
                    manifest_bytes = archive.extractfile(member).read()
                    (work_root / "manifest.json").write_bytes(manifest_bytes)
                    continue
                destination = work_root / Path(*PurePosixPath(member.name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target, _CHUNK_SIZE)
    except SnapshotValidationError:
        raise
    except (OSError, tarfile.TarError, AttributeError, TypeError) as exc:
        raise SnapshotValidationError("快照归档内容无效") from exc
    finally:
        archive_path.unlink(missing_ok=True)

    if "manifest.json" not in extracted_names or "database.sqlite3" not in extracted_names:
        raise SnapshotValidationError("快照缺少清单或数据库")
    try:
        manifest = SnapshotManifest.from_dict(json.loads((work_root / "manifest.json").read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SnapshotValidationError("快照清单无法解析") from exc
    _verify_manifest_signature(manifest)
    if expected_version is not None and manifest.app_version != expected_version:
        raise SnapshotValidationError("应用版本不兼容")
    if expected_key_version is not None and manifest.encryption_key_version != expected_key_version:
        raise SnapshotValidationError("复制密钥版本不兼容")
    if expected_migrations is not None and tuple(expected_migrations) != manifest.migration_versions:
        raise SnapshotValidationError("数据库迁移版本不兼容")
    if _sha256(work_root / "database.sqlite3") != manifest.database_sha256:
        raise SnapshotValidationError("快照数据库摘要不匹配")
    actual_media = {}
    for path in (work_root / "media").rglob("*"):
        if path.is_file():
            relative = path.relative_to(work_root / "media").as_posix()
            actual_media[relative] = {"size": path.stat().st_size, "sha256": _sha256(path)}
    if actual_media != manifest.media_files:
        raise SnapshotValidationError("快照媒体文件摘要不匹配")
    database = None
    try:
        database = sqlite3.connect(str(work_root / "database.sqlite3"))
        result = database.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise SnapshotValidationError("快照 SQLite 完整性检查失败")
    except sqlite3.DatabaseError as exc:
        raise SnapshotValidationError("快照 SQLite 数据库无法读取") from exc
    finally:
        if database is not None:
            database.close()
    return manifest


def validate_snapshot(package: Path, *, expected_version: str, expected_key_version: str | None = None, expected_migrations=None) -> SnapshotManifest:
    with tempfile.TemporaryDirectory(prefix="neon-dr-validate-") as temporary:
        return _decrypt_to_directory(
            package,
            Path(temporary),
            expected_version=expected_version,
            expected_key_version=expected_key_version,
            expected_migrations=expected_migrations,
        )


def validate_installed_replica(target_root: Path, *, expected_version: str, expected_key_version: str | None = None, expected_migrations=None) -> SnapshotManifest:
    active = Path(target_root) / "active"
    try:
        manifest = SnapshotManifest.from_dict(
            json.loads((active / "manifest.json").read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise SnapshotValidationError("热备当前副本清单无效") from exc
    _verify_manifest_signature(manifest)
    if manifest.app_version != expected_version:
        raise SnapshotValidationError("热备副本应用版本不兼容")
    if expected_key_version is not None and manifest.encryption_key_version != expected_key_version:
        raise SnapshotValidationError("热备副本密钥版本不兼容")
    if expected_migrations is not None and tuple(expected_migrations) != manifest.migration_versions:
        raise SnapshotValidationError("热备副本迁移版本不兼容")
    database_path = active / "database.sqlite3"
    if _sha256(database_path) != manifest.database_sha256:
        raise SnapshotValidationError("热备副本数据库摘要不匹配")
    database = None
    try:
        database = sqlite3.connect(str(database_path))
        result = database.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise SnapshotValidationError("热备副本 SQLite 完整性检查失败")
    except sqlite3.DatabaseError as exc:
        raise SnapshotValidationError("热备副本 SQLite 数据库无法读取") from exc
    finally:
        if database is not None:
            database.close()
    actual_media = {}
    media_root = active / "media"
    if media_root.exists():
        for path in media_root.rglob("*"):
            if path.is_symlink():
                raise SnapshotValidationError("热备媒体副本包含符号链接")
            if path.is_file():
                relative = path.relative_to(media_root).as_posix()
                actual_media[relative] = {"size": path.stat().st_size, "sha256": _sha256(path)}
    if actual_media != manifest.media_files:
        raise SnapshotValidationError("热备副本媒体摘要不匹配")
    return manifest


def install_snapshot(
    package: Path,
    target_root: Path,
    *,
    expected_version: str,
    expected_key_version: str | None = None,
    expected_migrations=None,
) -> Path:
    target_root = Path(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    staging = target_root / f".incoming-{uuid.uuid4().hex}"
    active = target_root / "active"
    previous_root = target_root / "previous"
    previous_root.mkdir(exist_ok=True)
    moved_old = None
    try:
        staging.mkdir()
        manifest = _decrypt_to_directory(
            package,
            staging,
            expected_version=expected_version,
            expected_key_version=expected_key_version,
            expected_migrations=expected_migrations,
        )
        active_manifest_path = active / "manifest.json"
        if active_manifest_path.is_file():
            try:
                active_manifest = SnapshotManifest.from_dict(
                    json.loads(active_manifest_path.read_text(encoding="utf-8"))
                )
                _verify_manifest_signature(active_manifest)
                active_order = (active_manifest.epoch, datetime.fromisoformat(active_manifest.created_at))
                incoming_order = (manifest.epoch, datetime.fromisoformat(manifest.created_at))
            except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
                raise SnapshotValidationError("现有副本清单无效，拒绝覆盖") from exc
            if incoming_order < active_order:
                raise SnapshotValidationError("收到的快照任期或创建时间早于当前副本，拒绝回滚")
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        if active.exists():
            moved_old = previous_root / uuid.uuid4().hex
            os.replace(active, moved_old)
        try:
            os.replace(staging, active)
        except OSError:
            if moved_old and moved_old.exists() and not active.exists():
                os.replace(moved_old, active)
            raise
    except SnapshotValidationError:
        raise
    except OSError as exc:
        raise SnapshotValidationError("快照安装失败，已保留原副本") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return active
