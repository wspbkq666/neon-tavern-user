#!/usr/bin/env bash

prompt_install_config() {
  local domain site_name email pairing_code_pattern forbidden_name_pattern
  read -r -p '公网域名（需已解析到本服务器）：' domain || return 20
  read -r -p '站点名称：' site_name || return 20
  read -r -p '证书续期联系邮箱：' email || return 20
  read -r -s -p '154 总站一次性配对码：' PAIRING_CODE_154 || return 20
  printf '\n' >&2
  read -r -s -p '123 总站一次性配对码：' PAIRING_CODE_123 || return 20
  printf '\n' >&2

  [[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || {
    log_error '域名格式无效'; return 20;
  }
  [[ "$email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$ ]] || {
    PAIRING_CODE_154=''; PAIRING_CODE_123=''
    log_error '证书联系邮箱格式无效'; return 20;
  }
  forbidden_name_pattern='["\\$`]'
  [[ -n "$site_name" && ! "$site_name" =~ [$'\n\r\t'] && ! "$site_name" =~ $forbidden_name_pattern && ${#site_name} -le 80 ]] || {
    PAIRING_CODE_154=''; PAIRING_CODE_123=''
    log_error '站点名称为空、过长或包含不支持的字符'; return 20;
  }
  pairing_code_pattern='^[[:alnum:]_.~-]{20,100}$'
  [[ "$PAIRING_CODE_154" =~ $pairing_code_pattern ]] || {
    PAIRING_CODE_154=''; PAIRING_CODE_123=''
    log_error '154 配对码格式无效，请重新获取'; return 20;
  }
  [[ "$PAIRING_CODE_123" =~ $pairing_code_pattern ]] || {
    PAIRING_CODE_154=''; PAIRING_CODE_123=''
    log_error '123 配对码格式无效，请重新获取'; return 20;
  }
  # shellcheck disable=SC2034
  INSTALL_DOMAIN="${domain,,}"
  # shellcheck disable=SC2034
  INSTALL_SITE_NAME="$site_name"
  INSTALL_EMAIL="$email"
}

create_instance_secrets() {
  local root="$1" domain="$2" site_name="$3" target credentials_dir staging code154 code123 forbidden_name_pattern
  local secret_key encryption_key
  code154="${PAIRING_CODE_154:-}"
  code123="${PAIRING_CODE_123:-}"
  target="$root/etc/neon-tavern-user"
  credentials_dir="$target/credentials"
  if [[ -e "$target" ]]; then
    log_error '实例配置目录已存在；拒绝覆盖已有配置或密钥'
    return 21
  fi
  [[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || {
    log_error '域名格式无效'; return 20;
  }
  forbidden_name_pattern='["\\$`]'
  [[ -n "$site_name" && ! "$site_name" =~ [$'\n\r\t'] && ! "$site_name" =~ $forbidden_name_pattern ]] || {
    log_error '站点名称包含不支持的字符'; return 20;
  }
  [[ "${#code154}" -ge 20 && "${#code123}" -ge 20 ]] || {
    log_error '请先输入两个总站的一次性配对码'; return 20;
  }
  command -v openssl >/dev/null 2>&1 || { log_error '缺少 openssl，无法生成实例密钥'; return 127; }

  umask 077
  mkdir -p "$root/etc"
  staging="$(mktemp -d "$root/etc/.neon-tavern-user.XXXXXX")" || return 21
  credentials_dir="$staging/credentials"
  if ! mkdir -m 0700 "$credentials_dir" ||
    ! printf '%s' "$code154" > "$credentials_dir/federation-pairing-code-154" ||
    ! printf '%s' "$code123" > "$credentials_dir/federation-pairing-code-123" ||
    ! openssl genpkey -algorithm Ed25519 -out "$credentials_dir/federation-private-key-154.pem" >/dev/null 2>&1 ||
    ! openssl genpkey -algorithm Ed25519 -out "$credentials_dir/federation-private-key-123.pem" >/dev/null 2>&1; then
    rm -rf "$staging"
    log_error '凭据生成失败；已清理未完成的临时文件'
    return 21
  fi

  secret_key="$(openssl rand -hex 48)" || { rm -rf "$staging"; return 21; }
  encryption_key="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')" || {
    rm -rf "$staging"; return 21;
  }
  {
    printf 'TAVERN_SECRET_KEY=%s\n' "$secret_key"
    printf 'TAVERN_ENCRYPTION_KEY=%s\n' "$encryption_key"
    printf 'TAVERN_DEBUG=0\n'
    printf 'TAVERN_ALLOWED_HOSTS=%s\n' "$domain"
    printf 'TAVERN_CSRF_ORIGINS=https://%s\n' "$domain"
    printf 'TAVERN_DB_PATH=/var/lib/neon-tavern-user/database.sqlite3\n'
    printf 'TAVERN_MEDIA_ROOT=/var/lib/neon-tavern-user/media\n'
    printf 'TAVERN_FEDERATION_ROLE=satellite\n'
    printf 'TAVERN_FEDERATION_CENTRAL_URL=https://154.222.26.47\n'
    printf 'TAVERN_FEDERATION_SITE_NAME="%s"\n' "$site_name"
    printf 'TAVERN_FEDERATION_SITE_URL=https://%s\n' "$domain"
    printf 'TAVERN_FEDERATION_SITE_KEY=%s\n' "$domain"
    printf 'TAVERN_CERTBOT_EMAIL=%s\n' "${INSTALL_EMAIL:-}"
  } > "$staging/environment"
  chmod 0600 "$staging/environment" "$credentials_dir"/*
  mv "$staging" "$target"
  PAIRING_CODE_154=''
  PAIRING_CODE_123=''
  log_info "已生成本站独立凭据：$target（密钥内容未显示）"
}
