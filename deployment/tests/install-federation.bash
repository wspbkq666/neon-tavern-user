#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT
NEON_TAVERN_INSTALLER_TEST=1 source "$ROOT/deployment/install.sh"

fail() { printf '失败：%s\n' "$*" >&2; exit 1; }
MOCK_BIN="$TEMP_ROOT/bin"
mkdir -p "$MOCK_BIN"
export MOCK_CURL_STATUS=503
cat > "$MOCK_BIN/curl" <<'MOCK'
#!/usr/bin/env bash
[[ "${MOCK_CURL_STATUS:-503}" == 200 ]] || exit 22
printf '{"ok":true,"federation":"registered","registered":true}\n'
MOCK
cat > "$MOCK_BIN/systemctl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_SYSTEMCTL_LOG"
exit 0
MOCK
chmod +x "$MOCK_BIN"/*
export PATH="$MOCK_BIN:$PATH"
export MOCK_SYSTEMCTL_LOG="$TEMP_ROOT/systemctl.log"
touch "$MOCK_SYSTEMCTL_LOG"

PREFIX="$TEMP_ROOT/root"
SYSTEMD_ROOT="$PREFIX/etc/systemd/system"
CREDENTIAL_ROOT="$PREFIX/etc/neon-tavern-user/credentials"
PAIRING_154='ONE-TIME-PAIRING-CODE-MAIN-154-123456'
PAIRING_123='ONE-TIME-PAIRING-CODE-MAIN-123-abcdef'
mkdir -p "$CREDENTIAL_ROOT"
printf '%s' "$PAIRING_154" > "$CREDENTIAL_ROOT/federation-pairing-code-154"
printf '%s' "$PAIRING_123" > "$CREDENTIAL_ROOT/federation-pairing-code-123"
openssl genpkey -algorithm Ed25519 -out "$CREDENTIAL_ROOT/federation-private-key-154.pem" 2>/dev/null
openssl genpkey -algorithm Ed25519 -out "$CREDENTIAL_ROOT/federation-private-key-123.pem" 2>/dev/null
chmod 0600 "$CREDENTIAL_ROOT"/*

install_federation_units "$PREFIX" || fail '双总站 systemd 单元安装失败'
WEB_DROPIN="$SYSTEMD_ROOT/neon-tavern-user-web.service.d/credentials.conf"
[[ -f "$WEB_DROPIN" ]] || fail 'Web credential drop-in 未安装'
grep -Fq 'TAVERN_FEDERATION_PAIRING_CODE_MAIN_154_FILE=%d/federation-pairing-code-154' "$WEB_DROPIN" || fail '缺少 154 一次性配对凭据映射'
grep -Fq 'TAVERN_FEDERATION_PAIRING_CODE_MAIN_123_FILE=%d/federation-pairing-code-123' "$WEB_DROPIN" || fail '缺少 123 一次性配对凭据映射'
grep -Fq 'TAVERN_FEDERATION_PRIVATE_KEY_FILE=%d/federation-private-key-154' "$WEB_DROPIN" || fail '缺少 154 私钥 credential'
grep -Fq 'TAVERN_FEDERATION_MAIN_123_PRIVATE_KEY_FILE=%d/federation-private-key-123' "$WEB_DROPIN" || fail '缺少 123 私钥 credential'
grep -Fq 'ExecStartPre=' "$ROOT/deployment/systemd/neon-tavern-user-web.service" || fail 'Web 启动前没有执行双站登记'
grep -Fq 'WantedBy=timers.target' "$SYSTEMD_ROOT/neon-tavern-user-chat-sync.timer" || fail '聊天同步 timer 未安装'
grep -Fq 'sync_chat_mirror' "$SYSTEMD_ROOT/neon-tavern-user-chat-sync.service" || fail '同步任务未调用应用管理命令'
grep -Fq 'poll_site_commands' "$SYSTEMD_ROOT/neon-tavern-user-site-command-poll.service" || fail '命令轮询未调用应用管理命令'

if finalize_federation "$PREFIX" 'https://tavern.example' 2>/dev/null; then
  fail '154/123 尚未都登记时应阻止最终验收'
fi
[[ -f "$CREDENTIAL_ROOT/federation-pairing-code-154" && -f "$CREDENTIAL_ROOT/federation-pairing-code-123" ]] || fail '部分登记失败时配对码被删除，无法重试'
! grep -Fq "$PAIRING_154" "$MOCK_SYSTEMCTL_LOG" || fail '配对码进入 systemctl 命令'

export MOCK_CURL_STATUS=200
finalize_federation "$PREFIX" 'https://tavern.example' || fail '双站健康后登记验收失败'
[[ ! -e "$CREDENTIAL_ROOT/federation-pairing-code-154" && ! -e "$CREDENTIAL_ROOT/federation-pairing-code-123" ]] || fail '双站登记成功后未删除一次性配对码'
! grep -Fq 'PAIRING_CODE_MAIN_154_FILE=' "$WEB_DROPIN" || fail '配对码仍由 systemd 加载'
grep -Fq 'TAVERN_FEDERATION_PRIVATE_KEY_FILE=%d/federation-private-key-154' "$WEB_DROPIN" || fail '一次性配对码清理时误删长期私钥配置'

printf '通过：双总站登记重试、systemd 凭据隔离与健康确认\n'
