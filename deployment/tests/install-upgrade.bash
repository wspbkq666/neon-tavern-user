#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT
NEON_TAVERN_INSTALLER_TEST=1 source "$ROOT/deployment/install.sh"

fail() { printf '失败：%s\n' "$*" >&2; exit 1; }
MOCK_BIN="$TEMP_ROOT/bin"
mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/systemctl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_SYSTEMCTL_LOG"
case "$*" in
  'start neon-tavern-user-web.service') [[ "${MOCK_START_FAIL:-0}" == 0 ]] ;;
  *) exit 0 ;;
esac
MOCK
cat > "$MOCK_BIN/curl" <<'MOCK'
#!/usr/bin/env bash
[[ "${MOCK_HEALTH_OK:-1}" == 1 ]] || exit 22
printf '{"ok":true,"federation":"registered","registered":true}\n'
MOCK
cat > "$MOCK_BIN/runuser" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_SYSTEMCTL_LOG"
while [[ "$#" -gt 0 && "$1" != -- ]]; do shift; done
[[ "${1:-}" == -- ]] || exit 2
shift
exec "$@"
MOCK
cat > "$MOCK_BIN/chown" <<'MOCK'
#!/usr/bin/env bash
[[ "${MOCK_CHOWN_FAIL:-0}" == 0 ]] || exit 1
exec -a chown /usr/bin/chown "$@"
MOCK
chmod +x "$MOCK_BIN"/*
export PATH="$MOCK_BIN:$PATH"
export MOCK_SYSTEMCTL_LOG="$TEMP_ROOT/systemctl.log"
touch "$MOCK_SYSTEMCTL_LOG"

APP_ROOT="$TEMP_ROOT/opt/neon-tavern-user"
BACKUP_ROOT="$TEMP_ROOT/backups"
DATA_ROOT="$TEMP_ROOT/data"
ENV_FILE="$TEMP_ROOT/environment"
LOCK_FILE="$TEMP_ROOT/lock"
OLD_RELEASE="$APP_ROOT/releases/old"
NEW_SHA='0123456789abcdef0123456789abcdef01234567'
NEW_RELEASE="$APP_ROOT/releases/$NEW_SHA"
CURRENT_LINK="$APP_ROOT/current"
DB_FILE="$DATA_ROOT/database.sqlite3"
mkdir -p "$OLD_RELEASE" "$NEW_RELEASE" "$NEW_RELEASE/venv/bin" "$DATA_ROOT"
touch "$OLD_RELEASE/manage.py" "$NEW_RELEASE/manage.py"
printf 'TAVERN_DB_PATH=%s\nTAVERN_SECRET_KEY=test\nTAVERN_ENCRYPTION_KEY=test\n' "$DB_FILE" > "$ENV_FILE"
rm -f "$DB_FILE"
rm -f "$DB_FILE"
python3 - "$DB_FILE" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("create table sample (value text)")
db.execute("insert into sample values ('before')")
db.commit()
db.close()
PY
ln -s "$OLD_RELEASE" "$CURRENT_LINK"
cat > "$NEW_RELEASE/venv/bin/python" <<'PYTHON'
#!/usr/bin/env bash
[[ "${MOCK_MIGRATE_FAIL:-0}" == 0 ]]
PYTHON
chmod +x "$NEW_RELEASE/venv/bin/python"

export NEON_APP_ROOT="$APP_ROOT" NEON_BACKUP_ROOT="$BACKUP_ROOT" NEON_DATA_ROOT="$DATA_ROOT"
export NEON_ENV_FILE="$ENV_FILE" NEON_INSTALL_LOCK="$LOCK_FILE" NEON_DATABASE_PATH="$DB_FILE"
export NEON_HEALTH_URL='https://tavern.example'
NEON_SERVICE_USER="$(id -un)"
NEON_SERVICE_GROUP="$(id -gn)"
export NEON_SERVICE_USER NEON_SERVICE_GROUP
export NEON_HEALTH_ATTEMPTS=1 NEON_FEDERATION_ROOT="$TEMP_ROOT/federation-root"
mkdir -p "$NEON_FEDERATION_ROOT/etc/neon-tavern-user/credentials"
mkdir -p "$NEON_FEDERATION_ROOT/etc/systemd/system/neon-tavern-user-web.service.d"
printf 'unused-after-registration\n' > "$NEON_FEDERATION_ROOT/etc/neon-tavern-user/credentials/federation-pairing-code-154"
printf 'unused-after-registration\n' > "$NEON_FEDERATION_ROOT/etc/neon-tavern-user/credentials/federation-pairing-code-123"
cat > "$NEON_FEDERATION_ROOT/etc/systemd/system/neon-tavern-user-web.service.d/credentials.conf" <<'CRED'
# Managed by Neon Tavern user-edition installer.
[Service]
LoadCredential=federation-private-key-154:/etc/neon-tavern-user/credentials/federation-private-key-154.pem
LoadCredential=federation-private-key-123:/etc/neon-tavern-user/credentials/federation-private-key-123.pem
LoadCredential=federation-pairing-code-154:/etc/neon-tavern-user/credentials/federation-pairing-code-154
LoadCredential=federation-pairing-code-123:/etc/neon-tavern-user/credentials/federation-pairing-code-123
CRED

printf 'not a sqlite database\n' > "$DB_FILE"
if upgrade_current "$NEW_SHA" 2>/dev/null; then fail '无效数据库备份必须阻止停服升级'; fi
[[ "$(readlink -f "$CURRENT_LINK")" == "$OLD_RELEASE" ]] || fail '备份失败时切换了 current'
! grep -Fq 'stop neon-tavern-user-web.service' "$MOCK_SYSTEMCTL_LOG" || fail '备份失败时停止了线上服务'

rm -f "$DB_FILE"
python3 - "$DB_FILE" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("create table sample (value text)")
db.execute("insert into sample values ('before')")
db.commit()
db.close()
PY
 : > "$MOCK_SYSTEMCTL_LOG"
MOCK_MIGRATE_FAIL=1
MOCK_CHOWN_FAIL=1
export MOCK_MIGRATE_FAIL MOCK_CHOWN_FAIL
if upgrade_current "$NEW_SHA" 2>/dev/null; then fail '数据库恢复失败必须中止升级'; fi
[[ "$(readlink -f "$CURRENT_LINK")" == "$OLD_RELEASE" ]] || fail '数据库恢复失败时 current 不应切换'
! grep -Fq 'start neon-tavern-user-web.service' "$MOCK_SYSTEMCTL_LOG" || fail '数据库恢复失败后仍启动了本站 Web 服务'
! grep -Fq 'start neon-tavern-user-worker.service' "$MOCK_SYSTEMCTL_LOG" || fail '数据库恢复失败后仍启动了本站 worker'
MOCK_CHOWN_FAIL=0
export MOCK_CHOWN_FAIL

MOCK_MIGRATE_FAIL=1
export MOCK_MIGRATE_FAIL
if upgrade_current "$NEW_SHA" 2>"$TEMP_ROOT/migration-upgrade.log"; then fail '迁移失败必须中止升级'; fi
cat "$TEMP_ROOT/migration-upgrade.log" >&2
[[ "$(readlink -f "$CURRENT_LINK")" == "$OLD_RELEASE" ]] || fail '迁移失败时切换了 current'
python3 - "$DB_FILE" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
assert db.execute("select value from sample").fetchone()[0] == "before"
PY
[[ -n "$(find "$BACKUP_ROOT" -type f -name database.sqlite3 -print -quit)" ]] || {
  find "$BACKUP_ROOT" -maxdepth 3 -type f -print >&2
  fail '未保留数据库备份'
}
! grep -E ' (restart|stop|start) (nginx|apache2|caddy)( |$)' "$MOCK_SYSTEMCTL_LOG" || fail '升级触碰了无关 Web 服务'

MOCK_MIGRATE_FAIL=0
MOCK_START_FAIL=1
export MOCK_MIGRATE_FAIL MOCK_START_FAIL
if upgrade_current "$NEW_SHA" 2>/dev/null; then fail 'Web 启动失败应报告未完成'; fi
[[ "$(readlink -f "$CURRENT_LINK")" == "$NEW_RELEASE" ]] || fail '数据库迁移后错误回切了不兼容旧版本'
grep -Fq 'stop neon-tavern-user-web.service neon-tavern-user-worker.service' "$MOCK_SYSTEMCTL_LOG" || fail '启动失败后没有停止本站服务'

MOCK_START_FAIL=0
MOCK_HEALTH_OK=0
export MOCK_START_FAIL MOCK_HEALTH_OK
if upgrade_current "$NEW_SHA" 2>/dev/null; then fail '健康检查失败应报告未完成'; fi
[[ "$(readlink -f "$CURRENT_LINK")" == "$NEW_RELEASE" ]] || fail '健康检查失败后未保留新版本和数据库兼容状态'

MOCK_HEALTH_OK=1
export MOCK_HEALTH_OK
upgrade_current "$NEW_SHA" || fail '健康检查通过后升级失败'
[[ "$(readlink -f "$CURRENT_LINK")" == "$NEW_RELEASE" ]] || fail '成功后 current 未指向目标 release'
[[ ! -e "$NEON_FEDERATION_ROOT/etc/neon-tavern-user/credentials/federation-pairing-code-154" ]] || fail '成功后未删除一次性配对码'

printf '通过：备份门禁、迁移失败恢复与服务隔离\n'
