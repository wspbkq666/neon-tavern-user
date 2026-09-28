#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT
NEON_TAVERN_INSTALLER_TEST=1 source "$ROOT/deployment/install.sh"

fail() { printf '失败：%s\n' "$*" >&2; exit 1; }
MOCK_BIN="$TEMP_ROOT/bin"
mkdir -p "$MOCK_BIN" "$TEMP_ROOT/source/core" "$TEMP_ROOT/source/deployment"
export MOCK_CALL_LOG="$TEMP_ROOT/calls"
export MOCK_SOURCE="$TEMP_ROOT/source"
NEON_SERVICE_USER="$(id -un)"
export NEON_SERVICE_USER
export MOCK_ACTUAL_SHA='0123456789abcdef0123456789abcdef01234567'
touch "$MOCK_CALL_LOG"
printf 'test requirement\n' > "$MOCK_SOURCE/requirements.txt"
printf 'print("fixture")\n' > "$MOCK_SOURCE/manage.py"
cp "$ROOT/deployment/verify_user_edition.py" "$MOCK_SOURCE/deployment/verify_user_edition.py"

cat > "$MOCK_BIN/git" <<'MOCK'
#!/usr/bin/env bash
case "$1" in
  clone)
    destination="${@: -1}"
    mkdir -p "$destination"
    mkdir -p "$destination/.git"
    cp -R "$MOCK_SOURCE"/. "$destination"/
    ;;
  -C)
    case "$3" in
      checkout) [[ "${MOCK_CHECKOUT_OK:-1}" == 1 ]] || exit 4 ;;
      rev-parse) printf '%s\n' "$MOCK_ACTUAL_SHA" ;;
      status) exit 0 ;;
      *) exit 2 ;;
    esac
    ;;
  *) exit 2 ;;
esac
MOCK
cat > "$MOCK_BIN/python3" <<'MOCK'
#!/usr/bin/env bash
if [[ "${1:-}" != -m || "${2:-}" != venv ]]; then exec /usr/bin/python3 "$@"; fi
venv="$3"
mkdir -p "$venv/bin"
cat > "$venv/bin/pip" <<'PIP'
#!/usr/bin/env bash
printf 'pip %s\n' "$*" >> "$MOCK_CALL_LOG"
[[ "${MOCK_PIP_FAIL:-0}" == 0 ]]
PIP
cat > "$venv/bin/python" <<'PYTHON'
#!/usr/bin/env bash
printf 'python %s\n' "$*" >> "$MOCK_CALL_LOG"
if [[ "${MOCK_DJANGO_FAIL:-}" == "${2:-}" ]]; then exit 1; fi
exit 0
PYTHON
chmod +x "$venv/bin/pip" "$venv/bin/python"
MOCK
cat > "$MOCK_BIN/runuser" <<'MOCK'
#!/usr/bin/env bash
printf 'runuser %s\n' "$*" >> "$MOCK_CALL_LOG"
while [[ "$#" -gt 0 && "$1" != -- ]]; do shift; done
[[ "${1:-}" == -- ]] || exit 2
shift
exec "$@"
MOCK
chmod +x "$MOCK_BIN"/*
export PATH="$MOCK_BIN:$PATH"

SHA='0123456789abcdef0123456789abcdef01234567'
DEST="$TEMP_ROOT/releases/good"
mkdir -p "$(dirname "$DEST")"
if fetch_release 'https://github.com/example/neon-tavern-user.git' "$SHA" "$SHA" "$TEMP_ROOT/releases/unfiltered" 2>/dev/null; then
  fail '缺少用户版发行清单的管理员仓库必须拒绝'
fi
[[ ! -e "$TEMP_ROOT/releases/unfiltered" ]] || fail '未通过用户版边界检查的源码残留在 release 目录'
printf '{"edition":"user","global_admin_code":"excluded","federation_required":true,"federation_centers":["154.222.26.47","123.56.125.209"]}\n' > "$MOCK_SOURCE/deployment/user-edition-release.json"
if fetch_release 'https://github.com/example/neon-tavern-user.git' "$SHA" "$SHA" "$DEST"; then
  :
else
  fail '有效固定 SHA 未检出'
fi
[[ -f "$DEST/manage.py" ]] || fail 'release 内容不完整'
fetch_release 'https://github.com/example/neon-tavern-user.git' "$SHA" "$SHA" "$DEST" || fail '同 SHA 安全重跑失败'
printf 'def is_global_admin(user): return True\n' > "$MOCK_SOURCE/core/auth_api.py"
if fetch_release 'https://github.com/example/neon-tavern-user.git' "$SHA" "$SHA" "$TEMP_ROOT/releases/global-admin-leak" 2>/dev/null; then
  fail '含全局管理员源码的 release 必须拒绝'
fi
[[ ! -e "$TEMP_ROOT/releases/global-admin-leak" ]] || fail '管理员源码边界失败后 release 目录未清理'
rm -f "$MOCK_SOURCE/core/auth_api.py"
grep -Fq 'Description=霓虹酒馆用户版 Web 服务' "$ROOT/deployment/systemd/neon-tavern-user-web.service" || fail '缺少独立 Web unit'
grep -Fq 'TAVERN_FEDERATION_ROLE' "$ROOT/deployment/systemd/neon-tavern-user-web.service" && fail 'systemd 模板不能允许 central 角色'
grep -Fq 'ReadWritePaths=/var/lib/neon-tavern-user' "$ROOT/deployment/systemd/neon-tavern-user-web.service" || fail 'Web 服务写入路径不受限'
grep -Fq 'run_generation_worker' "$ROOT/deployment/systemd/neon-tavern-user-worker.service" || fail '缺少独立生成 worker unit'

if fetch_release 'https://github.com/example/neon-tavern-user.git' main "$SHA" "$TEMP_ROOT/releases/floating" 2>/dev/null; then
  fail '未固定到完整 SHA 的版本应拒绝'
fi
MOCK_ACTUAL_SHA='ffffffffffffffffffffffffffffffffffffffff'
export MOCK_ACTUAL_SHA
if fetch_release 'https://github.com/example/neon-tavern-user.git' "$SHA" "$SHA" "$TEMP_ROOT/releases/wrong-sha" 2>/dev/null; then
  fail '检出的 commit 与请求 SHA 不同时应拒绝'
fi
MOCK_ACTUAL_SHA="$SHA"
export MOCK_ACTUAL_SHA

ENV_FILE="$TEMP_ROOT/environment"
printf 'TAVERN_DEBUG=0\nTAVERN_SECRET_KEY=test\nTAVERN_ENCRYPTION_KEY=test\n' > "$ENV_FILE"
prepare_release "$DEST" "$DEST/venv" "$ENV_FILE" || fail '正常 release 准备失败'
[[ ! -e "$TEMP_ROOT/current" ]] || fail '准备阶段不应切换 current'
[[ ! -e "$TEMP_ROOT/db.sqlite3" ]] || fail '准备阶段不应创建/删除业务数据库'

MOCK_PIP_FAIL=1
export MOCK_PIP_FAIL
if prepare_release "$DEST" "$TEMP_ROOT/failed-pip-venv" "$ENV_FILE" 2>/dev/null; then
  fail '依赖安装失败应中止'
fi
MOCK_PIP_FAIL=0
export MOCK_PIP_FAIL
MOCK_DJANGO_FAIL=check
export MOCK_DJANGO_FAIL
if prepare_release "$DEST" "$TEMP_ROOT/failed-check-venv" "$ENV_FILE" 2>/dev/null; then
  fail 'Django check 失败应中止'
fi
MOCK_DJANGO_FAIL=showmigrations
export MOCK_DJANGO_FAIL
if prepare_release "$DEST" "$TEMP_ROOT/failed-migration-venv" "$ENV_FILE" 2>/dev/null; then
  fail '迁移预检查失败应中止'
fi
[[ -f "$DEST/manage.py" ]] || fail '失败时旧 release 被删除'

printf '通过：固定 release、准备失败隔离与用户服务路径边界\n'
