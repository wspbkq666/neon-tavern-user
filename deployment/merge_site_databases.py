"""Merge one Neon Tavern site's user data into another SQLite database.

The target owns authentication for usernames already present there, as well as
its site identity and market credentials. This tool never copies sessions or
policy-consent records from the source.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


SPECIAL_TABLES = {
    "auth_user",
    "core_sitesettings",
    "core_usersettings",
    "core_userprofile",
}
SKIP_TABLES = {
    "django_migrations",
    "django_session",
    "django_content_type",
    "auth_permission",
}


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in sorted(
        (row for row in connection.execute(f'PRAGMA table_info("{table}")') if row[5]),
        key=lambda row: row[5],
    )]


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _latest_core_migration(connection: sqlite3.Connection) -> str | None:
    if "django_migrations" not in _table_names(connection):
        return None
    row = connection.execute(
        "SELECT name FROM django_migrations WHERE app='core' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _decode_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("预期 JSON 对象，数据库内容结构不兼容")
    return parsed


def _encrypt_for_target(value: str, source_cipher: Fernet, target_cipher: Fernet) -> str:
    if not value:
        return ""
    plaintext = source_cipher.decrypt(value.encode("ascii"))
    return target_cipher.encrypt(plaintext).decode("ascii")


def _merge_accounts(source: sqlite3.Connection, target: sqlite3.Connection) -> tuple[dict[int, int], dict[str, int]]:
    source_users = source.execute("SELECT * FROM auth_user ORDER BY id").fetchall()
    source_columns = _columns(source, "auth_user")
    target_columns = _columns(target, "auth_user")
    if source_columns != target_columns:
        raise ValueError("两站用户表结构不一致")

    target_by_name = {
        row["username"]: row["id"]
        for row in target.execute("SELECT id, username FROM auth_user")
    }
    target_by_fold = {name.casefold(): name for name in target_by_name}
    id_map: dict[int, int] = {}
    report = {"source_accounts": len(source_users), "new_accounts": 0, "matched_accounts": 0}

    for source_row in source_users:
        account = dict(zip(source_columns, source_row))
        username = account["username"]
        matched_name = target_by_fold.get(username.casefold())
        if matched_name and matched_name != username:
            raise ValueError("发现仅大小写不同的用户名冲突，停止合并以避免账号混淆")
        if username in target_by_name:
            id_map[account["id"]] = target_by_name[username]
            report["matched_accounts"] += 1
            continue

        fields = [name for name in source_columns if name != "id"]
        placeholders = ", ".join("?" for _ in fields)
        cursor = target.execute(
            f'INSERT INTO auth_user ({", ".join(chr(34) + field + chr(34) for field in fields)}) '
            f"VALUES ({placeholders})",
            [account[field] for field in fields],
        )
        id_map[account["id"]] = cursor.lastrowid
        target_by_name[username] = cursor.lastrowid
        report["new_accounts"] += 1
    return id_map, report


def _merge_user_settings(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    user_ids: dict[int, int],
    source_cipher: Fernet,
    target_cipher: Fernet,
) -> None:
    source_columns = _columns(source, "core_usersettings")
    target_columns = _columns(target, "core_usersettings")
    if source_columns != target_columns:
        raise ValueError("两站个人设置表结构不一致")
    for values in source.execute("SELECT * FROM core_usersettings"):
        row = dict(zip(source_columns, values))
        row["user_id"] = user_ids[row["user_id"]]
        row["encrypted_api_key"] = _encrypt_for_target(
            row["encrypted_api_key"], source_cipher, target_cipher
        )
        existing = target.execute(
            "SELECT * FROM core_usersettings WHERE user_id=?", (row["user_id"],)
        ).fetchone()
        if existing:
            current = dict(zip(target_columns, existing))
            merged = _decode_json(row["overrides"])
            merged.update(_decode_json(current["overrides"]))
            current["overrides"] = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
            if not current["encrypted_api_key"]:
                current["encrypted_api_key"] = row["encrypted_api_key"]
            current["updated_at"] = max(current["updated_at"], row["updated_at"])
            assignments = ", ".join(f'"{name}"=?' for name in target_columns if name not in {"id", "user_id"})
            fields = [name for name in target_columns if name not in {"id", "user_id"}]
            target.execute(
                f"UPDATE core_usersettings SET {assignments} WHERE user_id=?",
                [current[name] for name in fields] + [row["user_id"]],
            )
        else:
            fields = [name for name in source_columns if name != "id"]
            target.execute(
                f'INSERT INTO core_usersettings ({", ".join(chr(34) + field + chr(34) for field in fields)}) '
                f'VALUES ({", ".join("?" for _ in fields)})',
                [row[name] for name in fields],
            )


def _merge_profiles(
    source: sqlite3.Connection, target: sqlite3.Connection, user_ids: dict[int, int]
) -> None:
    source_columns = _columns(source, "core_userprofile")
    target_columns = _columns(target, "core_userprofile")
    if source_columns != target_columns:
        raise ValueError("两站用户资料表结构不一致")
    for values in source.execute("SELECT * FROM core_userprofile"):
        row = dict(zip(source_columns, values))
        row["user_id"] = user_ids[row["user_id"]]
        existing = target.execute(
            "SELECT * FROM core_userprofile WHERE user_id=?", (row["user_id"],)
        ).fetchone()
        if existing:
            current = dict(zip(target_columns, existing))
            if not current.get("inferred_traits"):
                current["inferred_traits"] = row.get("inferred_traits", "")
            current["updated_at"] = max(current["updated_at"], row["updated_at"])
            fields = [name for name in target_columns if name not in {"id", "user_id", "site_key", "policy_consent_at", "privacy_policy_version", "usage_rules_version"}]
            target.execute(
                "UPDATE core_userprofile SET "
                + ", ".join(f'"{name}"=?' for name in fields)
                + " WHERE user_id=?",
                [current[name] for name in fields] + [row["user_id"]],
            )
        else:
            # Site ownership is assigned locally on first login; consent is never imported.
            row["site_key"] = ""
            if "policy_consent_at" in row:
                row["policy_consent_at"] = None
                row["privacy_policy_version"] = ""
                row["usage_rules_version"] = ""
            fields = [name for name in source_columns if name != "id"]
            target.execute(
                f'INSERT INTO core_userprofile ({", ".join(chr(34) + field + chr(34) for field in fields)}) '
                f'VALUES ({", ".join("?" for _ in fields)})',
                [row[name] for name in fields],
            )


def _merge_site_settings(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    source_cipher: Fernet,
    target_cipher: Fernet,
) -> None:
    source_row = source.execute("SELECT * FROM core_sitesettings ORDER BY id LIMIT 1").fetchone()
    target_row = target.execute("SELECT * FROM core_sitesettings ORDER BY id LIMIT 1").fetchone()
    if not source_row or not target_row:
        return
    source_columns = _columns(source, "core_sitesettings")
    target_columns = _columns(target, "core_sitesettings")
    if source_columns != target_columns:
        raise ValueError("两站全局设置表结构不一致")
    incoming = dict(zip(source_columns, source_row))
    current = dict(zip(target_columns, target_row))
    values = _decode_json(incoming["values"])
    values.update(_decode_json(current["values"]))
    updates = {"values": json.dumps(values, ensure_ascii=False, separators=(",", ":"))}
    if not current["encrypted_api_key"] and incoming["encrypted_api_key"]:
        updates["encrypted_api_key"] = _encrypt_for_target(
            incoming["encrypted_api_key"], source_cipher, target_cipher
        )
    # Market identity and private signing credentials are intentionally target-owned.
    for name, value in updates.items():
        target.execute(f'UPDATE core_sitesettings SET "{name}"=? WHERE id=?', (value, current["id"]))


def _ordered_tables(source: sqlite3.Connection, tables: set[str]) -> list[str]:
    dependencies: dict[str, set[str]] = {}
    for table in tables:
        refs = {
            row[2]
            for row in source.execute(f'PRAGMA foreign_key_list("{table}")')
            if row[2] in tables and row[2] != table
        }
        dependencies[table] = refs
    result: list[str] = []
    while dependencies:
        ready = sorted(table for table, refs in dependencies.items() if not refs)
        if not ready:
            # M2M tables may form cycles; user mapping is already complete and the
            # foreign-key check after insertion remains authoritative.
            ready = [sorted(dependencies)[0]]
        for table in ready:
            result.append(table)
            dependencies.pop(table)
        for refs in dependencies.values():
            refs.difference_update(ready)
    return result


def _merge_remaining_tables(
    source: sqlite3.Connection, target: sqlite3.Connection, user_ids: dict[int, int]
) -> None:
    tables = (_table_names(source) & _table_names(target)) - SPECIAL_TABLES - SKIP_TABLES
    tables.discard("auth_user")
    for table in _ordered_tables(source, tables):
        source_columns = _columns(source, table)
        target_columns = _columns(target, table)
        if source_columns != target_columns:
            raise ValueError(f"数据表结构不一致：{table}")
        pk = _primary_key(source, table)
        info = {row[1]: row for row in source.execute(f'PRAGMA table_info("{table}")')}
        fk_user_columns = {
            row[3]
            for row in source.execute(f'PRAGMA foreign_key_list("{table}")')
            if row[2] == "auth_user"
        }
        for values in source.execute(f'SELECT * FROM "{table}"'):
            row = dict(zip(source_columns, values))
            for field in fk_user_columns:
                if row[field] is not None:
                    row[field] = user_ids[row[field]]

            fields = list(source_columns)
            if len(pk) == 1 and info[pk[0]][2].upper() == "INTEGER":
                fields.remove(pk[0])
            if pk and fields == source_columns:
                collision = target.execute(
                    f'SELECT * FROM "{table}" WHERE '
                    + " AND ".join(f'"{name}"=?' for name in pk),
                    [row[name] for name in pk],
                ).fetchone()
                if collision:
                    existing = dict(zip(target_columns, collision))
                    if all(existing[name] == row[name] for name in source_columns):
                        continue
                    raise ValueError(f"发现业务数据主键冲突：{table}")
            insert_mode = "INSERT OR IGNORE" if table in {
                "auth_user_groups",
                "auth_user_user_permissions",
            } else "INSERT"
            quoted_fields = ", ".join('"{}"'.format(name) for name in fields)
            target.execute(
                f'{insert_mode} INTO "{table}" ({quoted_fields}) '
                f'VALUES ({", ".join("?" for _ in fields)})',
                [row[name] for name in fields],
            )


def merge_site_databases(
    source_path: str | Path,
    target_path: str | Path,
    source_key: str | bytes,
    target_key: str | bytes,
    *,
    apply: bool = False,
) -> dict[str, int]:
    """Merge source records; dry-run by default, with all writes in one transaction."""
    source_path, target_path = Path(source_path), Path(target_path)
    if source_path.resolve() == target_path.resolve():
        raise ValueError("源数据库和目标数据库不能是同一个文件")
    source_cipher = Fernet(source_key.encode() if isinstance(source_key, str) else source_key)
    target_cipher = Fernet(target_key.encode() if isinstance(target_key, str) else target_key)
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    source.row_factory = target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    try:
        source.execute("BEGIN")
        source_tables, target_tables = _table_names(source), _table_names(target)
        required = {"auth_user", "core_sitesettings", "core_usersettings", "core_userprofile"}
        if not required <= source_tables or not required <= target_tables:
            raise ValueError("数据库缺少必要的用户或设置表")
        if _latest_core_migration(source) != _latest_core_migration(target):
            raise ValueError("两站 core 数据库迁移版本不一致")
        if apply:
            user_ids, report = _merge_accounts(source, target)
        else:
            report = _plan_accounts(source, target)
        if apply:
            _merge_user_settings(source, target, user_ids, source_cipher, target_cipher)
            _merge_profiles(source, target, user_ids)
            _merge_site_settings(source, target, source_cipher, target_cipher)
            _merge_remaining_tables(source, target, user_ids)
            violations = target.execute("PRAGMA foreign_key_check").fetchall()
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if violations or integrity != "ok":
                raise ValueError("合并后完整性检查失败")
            target.commit()
        return report
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def _plan_accounts(source: sqlite3.Connection, target: sqlite3.Connection) -> dict[str, int]:
    source_names = [row[0] for row in source.execute("SELECT username FROM auth_user")]
    target_names = {row[0] for row in target.execute("SELECT username FROM auth_user")}
    target_folds = {name.casefold(): name for name in target_names}
    for name in source_names:
        match = target_folds.get(name.casefold())
        if match and match != name:
            raise ValueError("发现仅大小写不同的用户名冲突，停止合并以避免账号混淆")
    matched = sum(name in target_names for name in source_names)
    return {
        "source_accounts": len(source_names),
        "new_accounts": len(source_names) - matched,
        "matched_accounts": matched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="合并霓虹酒馆站点数据库；默认仅预览")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--apply", action="store_true", help="实际写入目标库；运行前必须已有可验证备份")
    args = parser.parse_args()
    source_key = os.environ.get("TAVERN_SOURCE_ENCRYPTION_KEY")
    target_key = os.environ.get("TAVERN_TARGET_ENCRYPTION_KEY")
    if not source_key or not target_key:
        parser.error("请通过环境变量提供两站加密密钥，不要放入命令行参数")
    report = merge_site_databases(args.source, args.target, source_key, target_key, apply=args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
