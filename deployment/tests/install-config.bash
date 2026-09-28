#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT
NEON_TAVERN_INSTALLER_TEST=1 source "$ROOT/deployment/install.sh"

fail() { printf '失败：%s\n' "$*" >&2; exit 1; }

system_packages_output="$(system_packages)"
[[ " $system_packages_output " == *antiword* ]] || fail '系统依赖未包含 antiword'


LOG_FILE="$TEMP_ROOT/install.log"
PAIRING_154='PAIRING-SECRET-154-ONLY-1234567890'
PAIRING_123='PAIRING-SECRET-123-ONLY-ABCDEFGHIJ'
INPUT="$(printf 'tavern.example\n夜幕酒馆\nops@example.test\n%s\n%s\n' "$PAIRING_154" "$PAIRING_123")"
prompt_install_config <<< "$INPUT" 2>"$LOG_FILE" || fail '合法配置输入失败'

[[ "$INSTALL_DOMAIN" == tavern.example ]] || fail '域名未保存到普通配置'
[[ "$INSTALL_SITE_NAME" == 夜幕酒馆 ]] || fail '站点名称未保存到普通配置'
[[ "$PAIRING_CODE_154" == "$PAIRING_154" && "$PAIRING_CODE_123" == "$PAIRING_123" ]] || fail '隐藏配对码未读取'

SECRET_ROOT="$TEMP_ROOT/root"
create_instance_secrets "$SECRET_ROOT" "$INSTALL_DOMAIN" "$INSTALL_SITE_NAME" >>"$LOG_FILE" 2>&1 || fail '凭据生成失败'
ENV_FILE="$SECRET_ROOT/etc/neon-tavern-user/environment"
CREDENTIAL_DIR="$SECRET_ROOT/etc/neon-tavern-user/credentials"

[[ -f "$ENV_FILE" ]] || fail '环境文件未生成'
[[ "$(stat -c '%a' "$ENV_FILE")" == 600 ]] || fail '环境文件权限不是 0600'
[[ "$(stat -c '%a' "$CREDENTIAL_DIR/federation-pairing-code-154")" == 600 ]] || fail '154 配对码权限不是 0600'
[[ "$(stat -c '%a' "$CREDENTIAL_DIR/federation-pairing-code-123")" == 600 ]] || fail '123 配对码权限不是 0600'
[[ "$(stat -c '%a' "$CREDENTIAL_DIR/federation-private-key-154.pem")" == 600 ]] || fail '154 私钥权限不是 0600'
[[ "$(stat -c '%a' "$CREDENTIAL_DIR/federation-private-key-123.pem")" == 600 ]] || fail '123 私钥权限不是 0600'
[[ "$(cat "$CREDENTIAL_DIR/federation-pairing-code-154")" == "$PAIRING_154" ]] || fail '154 配对码文件内容不符'
[[ "$(cat "$CREDENTIAL_DIR/federation-pairing-code-123")" == "$PAIRING_123" ]] || fail '123 配对码文件内容不符'

grep -Fxq 'TAVERN_FEDERATION_ROLE=satellite' "$ENV_FILE" || fail '站点角色不是 satellite'
grep -Fxq 'TAVERN_CERTBOT_EMAIL=ops@example.test' "$ENV_FILE" || fail '证书邮箱未保存'
grep -Fxq 'TAVERN_FEDERATION_CENTRAL_URL=https://154.222.26.47' "$ENV_FILE" || fail '154 总站地址被改变'
grep -Fq '"main_123": "https://123.56.125.209"' "$ROOT/core/site_federation_client.py" || fail '应用未固定 123 总站地址'
! grep -q 'TAVERN_FEDERATION_MAIN_123_URL=' "$ENV_FILE" || fail '环境文件试图覆盖固定总站地址'
grep -Fxq 'TAVERN_FEDERATION_SITE_URL=https://tavern.example' "$ENV_FILE" || fail '站点 URL 未使用 HTTPS'
grep -Fxq 'TAVERN_FEDERATION_SITE_KEY=tavern.example' "$ENV_FILE" || fail '站点身份键错误'
grep -Fxq 'TAVERN_DB_PATH=/var/lib/neon-tavern-user/database.sqlite3' "$ENV_FILE" || fail '数据库路径不在独立数据目录'

pub_154="$(openssl pkey -in "$CREDENTIAL_DIR/federation-private-key-154.pem" -pubout 2>/dev/null)"
pub_123="$(openssl pkey -in "$CREDENTIAL_DIR/federation-private-key-123.pem" -pubout 2>/dev/null)"
[[ "$pub_154" != "$pub_123" ]] || fail '两站共用了同一把私钥'
for secret in "$PAIRING_154" "$PAIRING_123" "$pub_154" "$pub_123"; do
  ! grep -Fq -- "$secret" "$LOG_FILE" || fail '安装输出泄露了凭据或私钥'
done
[[ -z "$PAIRING_CODE_154" && -z "$PAIRING_CODE_123" ]] || fail '配对成功后仍在 shell 变量中保留配对码'

if printf 'bad;domain\n夜幕酒馆\ncode-a\ncode-b\n' | prompt_install_config >/dev/null 2>&1; then
  fail '非法域名未被拒绝'
fi

printf '通过：隐藏输入、密钥权限、固定分站配置与凭据不泄漏\n'
