#!/usr/bin/env python3
"""Fail closed when a user release contains global-admin capabilities."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


EXPECTED_CENTERS = ["154.222.26.47", "123.56.125.209"]
SOURCE_SUFFIXES = {".html", ".js", ".mjs", ".py"}
FORBIDDEN = re.compile(
    r"\bis_global_admin\b"
    r"|\bisGlobalAdmin\b"
    r"|\bglobal_admin\b"
    r"|\bTAVERN_GLOBAL_ADMIN_USERNAME\b"
    r"|data-global-admin-only"
    r"|/api/update-policy(?:/|['\"?#]|$)"
    r"|/api/admin/(?:deployment-info|update-policy|site-admins|sites)(?:/|['\"?#]|$)"
    r"|\b(?:showSiteAdmins|showUpdates|showDeploymentInfo)\b",
    re.IGNORECASE,
)
SKIP_PARTS = {"test", "tests", "__pycache__"}


def _manifest_issues(root: Path) -> list[str]:
    manifest_path = root / "deployment" / "user-edition-release.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return ["缺少有效的用户版发行清单"]
    if not isinstance(manifest, dict):
        return ["用户版发行清单必须是 JSON 对象"]

    issues = []
    if manifest.get("edition") != "user":
        issues.append("发行清单 edition 必须为 user")
    if manifest.get("global_admin_code") != "excluded":
        issues.append("发行清单必须声明已排除全局管理员代码")
    if manifest.get("federation_required") is not True:
        issues.append("发行清单必须声明双总站监管为必需项")
    if manifest.get("federation_centers") != EXPECTED_CENTERS:
        issues.append("发行清单中的双总站地址与固定地址不一致")
    return issues


def inspect_release(root: Path) -> list[str]:
    root = Path(root).resolve()
    issues = _manifest_issues(root)
    source_roots = [root / name for name in ("backend", "core", "frontend_dist", "tavern")]
    for source_root in source_roots:
        if not source_root.exists():
            continue
        if not source_root.is_dir():
            issues.append(f"源码目录不是文件夹：{source_root.relative_to(root)}")
            continue
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(root)
            if any(part in SKIP_PARTS or part.startswith("test_") for part in relative.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                issues.append(f"无法以 UTF-8 检查源码：{relative.as_posix()}")
                continue
            match = FORBIDDEN.search(content)
            if match:
                issues.append(f"含有总站专属权限/接口标记：{relative.as_posix()} ({match.group(0)})")
    return issues


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：python3 deployment/verify_user_edition.py <用户版源码目录>", file=sys.stderr)
        return 2
    issues = inspect_release(Path(argv[1]))
    if issues:
        for issue in issues:
            print(f"失败：{issue}", file=sys.stderr)
        return 1
    print("通过：发行清单有效，未发现全局管理员代码，双总站监管为必需项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
