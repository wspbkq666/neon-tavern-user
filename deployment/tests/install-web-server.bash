#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT
NEON_TAVERN_INSTALLER_TEST=1 source "$ROOT/deployment/install.sh"

fail() { printf '失败：%s\n' "$*" >&2; exit 1; }
MOCK_BIN="$TEMP_ROOT/bin"
mkdir -p "$MOCK_BIN" "$TEMP_ROOT/nginx/sites-available" "$TEMP_ROOT/nginx/sites-enabled" "$TEMP_ROOT/webroot"
export MOCK_CALLS="$TEMP_ROOT/calls"
touch "$MOCK_CALLS"
cat > "$MOCK_BIN/nginx" <<'MOCK'
#!/usr/bin/env bash
case "$1" in
  -T)
    [[ "${MOCK_NGINX_PARSE_OK:-1}" == 1 ]] || exit 1
    if [[ "${MOCK_DOMAIN_CONFLICT:-0}" == 1 ]]; then printf 'server_name tavern.example;\n'; fi
    ;;
  -t) [[ "${MOCK_NGINX_TEST_OK:-1}" == 1 ]] ;;
  *) exit 2 ;;
esac
MOCK
cat > "$MOCK_BIN/systemctl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_CALLS"
[[ "$*" == 'reload nginx' ]]
MOCK
cat > "$MOCK_BIN/certbot" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MOCK_CALLS"
[[ "${MOCK_CERTBOT_OK:-1}" == 1 ]]
MOCK
chmod +x "$MOCK_BIN"/*
export PATH="$MOCK_BIN:$PATH"
NGINX_SITES_AVAILABLE="$TEMP_ROOT/nginx/sites-available"
NGINX_SITES_ENABLED="$TEMP_ROOT/nginx/sites-enabled"
NEON_ACME_WEBROOT="$TEMP_ROOT/webroot"
NEON_STATIC_ROOT='/opt/neon-tavern-user/current/collected_static'
DOMAIN=tavern.example
CONFIG_PATH="$NGINX_SITES_AVAILABLE/neon-tavern-user-$DOMAIN"

rendered="$(render_nginx_site "$DOMAIN" "$NEON_STATIC_ROOT")" || fail '无法生成 Nginx 配置'
[[ "$rendered" == *"server_name $DOMAIN;"* ]] || fail '配置未绑定站点域名'
[[ "$rendered" == *'proxy_pass http://127.0.0.1:18087;'* ]] || fail '反向代理目标端口错误'
[[ "$rendered" == *"alias $NEON_STATIC_ROOT/;"* ]] || fail '静态资源路径错误'

printf '原有默认站点\n' > "$TEMP_ROOT/nginx/default"
cp "$TEMP_ROOT/nginx/default" "$TEMP_ROOT/default.before"
MOCK_DOMAIN_CONFLICT=1
export MOCK_DOMAIN_CONFLICT
if install_nginx_site "$DOMAIN" "$CONFIG_PATH" 2>/dev/null; then fail '同域名 Nginx 配置应冲突退出'; fi
unset MOCK_DOMAIN_CONFLICT
cmp -s "$TEMP_ROOT/default.before" "$TEMP_ROOT/nginx/default" || fail '默认站点被修改'
[[ ! -e "$CONFIG_PATH" ]] || fail '冲突时创建了配置文件'

printf '未知的现存配置\n' > "$CONFIG_PATH"
cp "$CONFIG_PATH" "$TEMP_ROOT/unknown.before"
if install_nginx_site "$DOMAIN" "$CONFIG_PATH" 2>/dev/null; then fail '未知配置应拒绝覆盖'; fi
cmp -s "$TEMP_ROOT/unknown.before" "$CONFIG_PATH" || fail '未知现存配置被覆盖'
rm "$CONFIG_PATH"

MOCK_NGINX_TEST_OK=0
export MOCK_NGINX_TEST_OK
if install_nginx_site "$DOMAIN" "$CONFIG_PATH" 2>/dev/null; then fail 'nginx -t 失败应中止'; fi
unset MOCK_NGINX_TEST_OK
[[ ! -e "$CONFIG_PATH" && ! -L "$NGINX_SITES_ENABLED/neon-tavern-user-$DOMAIN" ]] || fail '语法错误后残留了本站配置'
! grep -Fq 'reload nginx' "$MOCK_CALLS" || fail '语法检查失败时 reload 了 Nginx'

MOCK_CERTBOT_OK=0
export MOCK_CERTBOT_OK
if provision_nginx_site "$DOMAIN" "$CONFIG_PATH" "$NEON_STATIC_ROOT" 'ops@example.test' 2>/dev/null; then
  fail '签发证书失败后不应完成站点部署'
fi
unset MOCK_CERTBOT_OK
[[ ! -e "$CONFIG_PATH" && ! -L "$NGINX_SITES_ENABLED/neon-tavern-user-$DOMAIN" ]] || fail '证书申请失败后残留了启用站点'

install_nginx_site "$DOMAIN" "$CONFIG_PATH" || fail '安全的独立站点安装失败'
[[ -L "$NGINX_SITES_ENABLED/neon-tavern-user-$DOMAIN" ]] || fail '缺少本站专属 enabled 链接'
[[ "$(<"$TEMP_ROOT/nginx/default")" == '原有默认站点' ]] || fail '默认站点内容变化'
grep -Fxq 'reload nginx' "$MOCK_CALLS" || fail '配置检查通过后未 reload Nginx'

MOCK_CERTBOT_OK=0
export MOCK_CERTBOT_OK
if obtain_certificate "$DOMAIN" "$NEON_ACME_WEBROOT" 'ops@example.test' 2>/dev/null; then fail '证书申请失败应返回错误'; fi
unset MOCK_CERTBOT_OK
grep -Fq 'certonly --webroot' "$MOCK_CALLS" || fail '证书未使用 webroot 验证'
! grep -Fq 'restart nginx' "$MOCK_CALLS" || fail '证书流程重启了 Nginx'

printf '通过：Nginx 站点隔离、失败回滚与 webroot 证书流程\n'
