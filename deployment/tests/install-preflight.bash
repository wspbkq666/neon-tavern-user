#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="$ROOT/deployment/install.sh"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT

fail() {
  printf '失败：%s\n' "$*" >&2
  exit 1
}

[[ -f "$INSTALLER" ]] || fail '安装器入口尚未实现'
# shellcheck disable=SC1090
NEON_TAVERN_INSTALLER_TEST=1 source "$INSTALLER"

MOCK_BIN="$TEMP_ROOT/bin"
mkdir -p "$MOCK_BIN" "$TEMP_ROOT/etc/nginx/sites-enabled" "$TEMP_ROOT/systemd"
export MOCK_CALLS="$TEMP_ROOT/calls"
export MOCK_NGINX_ACTIVE=1
touch "$MOCK_CALLS"

cat > "$MOCK_BIN/uname" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "${MOCK_ARCH:-x86_64}"
MOCK
cat > "$MOCK_BIN/getent" <<'MOCK'
#!/usr/bin/env bash
[[ "${MOCK_DNS_OK:-1}" == 1 ]] || exit 2
printf '203.0.113.10 STREAM example.test\n'
MOCK
cat > "$MOCK_BIN/systemctl" <<'MOCK'
#!/usr/bin/env bash
case "$1 ${2:-}" in
  'start '*|'stop '*|'restart '*|'reload '*|'enable '*|'disable '*|'daemon-reload '*)
    printf '%s\n' "$*" >> "$MOCK_CALLS"
    ;;
esac
case "$1" in
  'is-system-running') [[ "${MOCK_SYSTEMD_OK:-1}" == 1 ]]; exit $? ;;
  'is-active')
    [[ "${*: -1}" == nginx && "${MOCK_NGINX_ACTIVE:-0}" == 1 ]] && exit 0
    [[ "${*: -1}" == neon-tavern-user-web.service && "${MOCK_TAVERN_ACTIVE:-0}" == 1 ]] && exit 0
    exit 1
    ;;
  show)
    [[ "${*: -1}" == neon-tavern-user-web.service ]] || exit 1
    printf '%s\n' "${MOCK_MAINPID:-0}"
    exit 0
esac
case "$1" in
  cat)
    [[ "${MOCK_UNIT_EXISTS:-0}" == 1 ]] && printf '[Service]\n' && exit 0
    exit 1
    ;;
  *) exit 0 ;;
esac
MOCK
cat > "$MOCK_BIN/ss" <<'MOCK'
#!/usr/bin/env bash
if [[ "${MOCK_PORT_CONFLICT:-0}" == 1 ]]; then
  if [[ -n "${MOCK_EXTRA_LISTENER_PID:-}" ]]; then
    printf 'LISTEN 0 128 0.0.0.0:%s 0.0.0.0:* users:(("%s",pid=%s,fd=3),("%s",pid=%s,fd=4))\n' \
      "${MOCK_CONFLICT_PORT:-443}" "${MOCK_PROCESS:-python}" "$MOCK_EXTRA_LISTENER_PID" "${MOCK_PROCESS:-python}" "${MOCK_LISTENER_PID:-42}"
  else
    printf 'LISTEN 0 128 0.0.0.0:%s 0.0.0.0:* users:(("%s",pid=%s,fd=3))\n' "${MOCK_CONFLICT_PORT:-443}" "${MOCK_PROCESS:-python}" "${MOCK_LISTENER_PID:-42}"
  fi
else
  printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n'
fi
MOCK
cat > "$MOCK_BIN/nginx" <<'MOCK'
#!/usr/bin/env bash
[[ "${MOCK_NGINX_OK:-1}" == 1 ]] || exit 1
if [[ "$*" == *'-T'* && "${MOCK_NGINX_DOMAIN_CONFLICT:-0}" == 1 ]]; then
  printf 'server_name example.test;\n'
fi
MOCK
chmod +x "$MOCK_BIN"/*
export PATH="$MOCK_BIN:$PATH"

OS_RELEASE_FILE="$TEMP_ROOT/os-release"
# These globals configure the sourced installer modules.
# shellcheck disable=SC2034
SYSTEMD_DIR="$TEMP_ROOT/systemd"
# shellcheck disable=SC2034
NGINX_SITES_ENABLED="$TEMP_ROOT/etc/nginx/sites-enabled"
DOMAIN=example.test
printf 'ID=ubuntu\nVERSION_ID="22.04"\n' > "$OS_RELEASE_FILE"

assert_preflight_fails() {
  local label="$1"
  if run_preflight "$DOMAIN" >/dev/null 2>&1; then
    fail "$label 未被拒绝"
  fi
}

assert_preflight_passes() {
  local label="$1" output
  output="$(run_preflight "$DOMAIN" 2>&1)" || fail "$label 意外失败：$output"
}

assert_preflight_passes '合法环境'

printf 'ID=ubuntu\nVERSION_ID="20.04"\n' > "$OS_RELEASE_FILE"
assert_preflight_fails 'Ubuntu 20.04'
printf 'ID=ubuntu\nVERSION_ID="22.04"\n' > "$OS_RELEASE_FILE"

MOCK_ARCH=aarch64
export MOCK_ARCH
assert_preflight_fails '非 x86_64 架构'
unset MOCK_ARCH

MOCK_SYSTEMD_OK=0
export MOCK_SYSTEMD_OK
assert_preflight_fails 'systemd 不可用'
unset MOCK_SYSTEMD_OK

MOCK_DNS_OK=0
export MOCK_DNS_OK
assert_preflight_fails '无 DNS A 记录'
unset MOCK_DNS_OK

MOCK_PORT_CONFLICT=1
export MOCK_PORT_CONFLICT
assert_preflight_fails '80/443 由非 Nginx 进程占用'
unset MOCK_PORT_CONFLICT

MOCK_PORT_CONFLICT=1
MOCK_CONFLICT_PORT=18087
export MOCK_PORT_CONFLICT MOCK_CONFLICT_PORT
assert_preflight_fails '霓虹酒馆专用后端端口被占用'
unset MOCK_PORT_CONFLICT MOCK_CONFLICT_PORT

MOCK_PORT_CONFLICT=1
MOCK_CONFLICT_PORT=18087
MOCK_TAVERN_ACTIVE=1
MOCK_MAINPID=77
MOCK_LISTENER_PID=77
MOCK_PROCESS=gunicorn
NEON_UPGRADE_MODE=1
export MOCK_PORT_CONFLICT MOCK_CONFLICT_PORT MOCK_TAVERN_ACTIVE MOCK_MAINPID MOCK_LISTENER_PID MOCK_PROCESS NEON_UPGRADE_MODE
assert_preflight_passes '升级时本站 systemd 主进程持有专用端口'
MOCK_EXTRA_LISTENER_PID=88
export MOCK_EXTRA_LISTENER_PID
assert_preflight_passes '升级时 Gunicorn worker 排在 MainPID 前'
unset MOCK_EXTRA_LISTENER_PID
MOCK_LISTENER_PID=88
export MOCK_LISTENER_PID
assert_preflight_fails '升级时其他进程持有本站专用端口'
unset MOCK_PORT_CONFLICT MOCK_CONFLICT_PORT MOCK_TAVERN_ACTIVE MOCK_MAINPID MOCK_LISTENER_PID MOCK_PROCESS NEON_UPGRADE_MODE

MOCK_UNIT_EXISTS=1
export MOCK_UNIT_EXISTS
assert_preflight_fails '已有同名霓虹酒馆 systemd unit'
unset MOCK_UNIT_EXISTS

MOCK_NGINX_DOMAIN_CONFLICT=1
export MOCK_NGINX_DOMAIN_CONFLICT
assert_preflight_fails 'Nginx 已有同域名配置'
unset MOCK_NGINX_DOMAIN_CONFLICT

[[ ! -s "$MOCK_CALLS" ]] || fail '预检执行了 systemctl/nginx 变更命令'
[[ -z "$(find "$TEMP_ROOT/etc/nginx/sites-enabled" -mindepth 1 -print -quit)" ]] || fail '预检改动了 Nginx 配置目录'
[[ -z "$(find "$TEMP_ROOT/systemd" -mindepth 1 -print -quit)" ]] || fail '预检改动了 systemd 配置目录'

printf '通过：预检只读边界与所有失败场景\n'
