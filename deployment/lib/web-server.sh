#!/usr/bin/env bash

render_nginx_site() {
  local domain="$1" static_root="$2"
  [[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || return 40
  [[ "$static_root" == /* && "$static_root" != *[$'\n\r']* ]] || return 40
  cat <<NGINX
# Managed by Neon Tavern user-edition installer.
server {
    listen 80;
    server_name $domain;

    location /.well-known/acme-challenge/ {
        root ${NEON_ACME_WEBROOT:-/var/www/neon-tavern-user-acme};
        try_files \$uri =404;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name $domain;

    ssl_certificate /etc/letsencrypt/live/$domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$domain/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 21m;

    location /static/ {
        alias $static_root/;
        expires 1h;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:18087;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 130s;
    }
}
NGINX
}

render_nginx_challenge_site() {
  local domain="$1"
  cat <<NGINX
# Managed by Neon Tavern user-edition installer.
server {
    listen 80;
    server_name $domain;
    location /.well-known/acme-challenge/ {
        root ${NEON_ACME_WEBROOT:-/var/www/neon-tavern-user-acme};
        try_files \$uri =404;
    }
    location / { return 404; }
}
NGINX
}

_nginx_domain_conflict() {
  local domain="$1" excluded_path="${2:-}" output line source_file=''
  output="$(nginx -T 2>&1)" || { log_error 'nginx -T 检查失败'; return 1; }
  while IFS= read -r line; do
    if [[ "$line" =~ ^#[[:space:]]configuration[[:space:]]file[[:space:]](.+):$ ]]; then
      source_file="${BASH_REMATCH[1]}"
    fi
    [[ "$line" =~ ^[[:space:]]*server_name[[:space:]] ]] || continue
    line="${line#*server_name}"
    line="${line%%;*}"
    for token in $line; do
      if [[ "$token" == "$domain" || "$token" == "*.$domain" || "$token" == ".$domain" ]]; then
        [[ -n "$excluded_path" && "$source_file" == "$excluded_path" ]] && continue
        return 0
      fi
    done
  done <<< "$output"
  return 1
}

install_nginx_site() {
  local domain="$1" config_path="$2" static_root="${3:-${NEON_STATIC_ROOT:-/opt/neon-tavern-user/current/collected_static}}"
  local enabled_dir="${NGINX_SITES_ENABLED:-/etc/nginx/sites-enabled}" enabled_path temp_path backup_path had_config=0
  [[ "$config_path" == */neon-tavern-user-"$domain" ]] || { log_error 'Nginx 配置路径不是本站专属路径'; return 40; }
  mkdir -p "$(dirname "$config_path")" "$enabled_dir"
  enabled_path="$enabled_dir/neon-tavern-user-$domain"
  if [[ -e "$config_path" || -L "$config_path" ]]; then
    [[ -f "$config_path" && ! -L "$config_path" ]] || { log_error '本站配置路径是未知文件类型，拒绝覆盖'; return 40; }
    grep -Fq '# Managed by Neon Tavern user-edition installer.' "$config_path" || {
      log_error '目标路径已有非安装器管理的配置，拒绝覆盖'; return 40;
    }
    [[ "${NEON_ALLOW_CONFIG_UPGRADE:-0}" == 1 ]] || { log_error '本站已有配置；需显式批准更新'; return 40; }
    had_config=1
  fi
  if [[ -e "$enabled_path" || -L "$enabled_path" ]]; then
    [[ -L "$enabled_path" && "$(readlink -f "$enabled_path")" == "$(readlink -m "$config_path")" ]] || {
      log_error '本站 enabled 路径已被其他内容占用'; return 40;
    }
  fi
  if [[ "$had_config" == 0 ]] && _nginx_domain_conflict "$domain"; then
    log_error "Nginx 已有站点占用域名 $domain"; return 40
  elif [[ "$had_config" == 1 ]] && _nginx_domain_conflict "$domain" "$enabled_path"; then
    log_error "另一份 Nginx 配置已占用域名 $domain"; return 40
  fi

  temp_path="$(mktemp "$(dirname "$config_path")/.neon-tavern-user.XXXXXX")" || return 40
  render_nginx_site "$domain" "$static_root" > "$temp_path" || { rm -f "$temp_path"; return 40; }
  chmod 0644 "$temp_path"
  if [[ "$had_config" == 1 ]]; then
    backup_path="$config_path.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    [[ ! -e "$backup_path" ]] || { rm -f "$temp_path"; log_error 'Nginx 备份路径已存在'; return 40; }
    cp -p "$config_path" "$backup_path" || { rm -f "$temp_path"; return 40; }
  fi
  mv -f "$temp_path" "$config_path"
  if [[ ! -e "$enabled_path" && ! -L "$enabled_path" ]]; then
    if ! ln -s "$config_path" "$enabled_path"; then
      if [[ "$had_config" == 1 ]]; then cp -p "$backup_path" "$config_path"; else rm -f "$config_path"; fi
      return 40
    fi
    local created_link=1
  else
    local created_link=0
  fi
  if ! nginx -t; then
    [[ "$created_link" == 1 ]] && rm -f "$enabled_path"
    if [[ "$had_config" == 1 ]]; then cp -p "$backup_path" "$config_path"; else rm -f "$config_path"; fi
    log_error 'Nginx 语法检查失败；已恢复本站原配置，未 reload'
    return 41
  fi
  if ! systemctl reload nginx; then
    [[ "$created_link" == 1 ]] && rm -f "$enabled_path"
    if [[ "$had_config" == 1 ]]; then cp -p "$backup_path" "$config_path"; else rm -f "$config_path"; fi
    log_error 'Nginx reload 失败；已恢复本站原配置'
    return 41
  fi
}

obtain_certificate() {
  local domain="$1" webroot="$2" email="$3"
  command -v certbot >/dev/null 2>&1 || { log_error '缺少 certbot'; return 42; }
  certbot certonly --webroot --webroot-path "$webroot" --domain "$domain" \
    --non-interactive --agree-tos --email "$email" || {
      log_error '证书申请失败；请检查域名解析、防火墙和 80 端口可达性'; return 42;
    }
}

install_nginx_challenge_site() {
  local domain="$1" config_path="$2" enabled_dir="${NGINX_SITES_ENABLED:-/etc/nginx/sites-enabled}"
  local enabled_path temporary
  [[ "$config_path" == */neon-tavern-user-"$domain" ]] || return 40
  mkdir -p "$(dirname "$config_path")" "$enabled_dir" "${NEON_ACME_WEBROOT:-/var/www/neon-tavern-user-acme}"
  enabled_path="$enabled_dir/neon-tavern-user-$domain"
  [[ ! -e "$config_path" && ! -L "$config_path" && ! -e "$enabled_path" && ! -L "$enabled_path" ]] || {
    log_error '本站或 enabled 路径已存在，拒绝替换'; return 40;
  }
  if _nginx_domain_conflict "$domain"; then log_error "Nginx 已有站点占用域名 $domain"; return 40; fi
  temporary="$(mktemp "$(dirname "$config_path")/.neon-tavern-user.XXXXXX")" || return 40
  render_nginx_challenge_site "$domain" > "$temporary"
  chmod 0644 "$temporary"
  mv "$temporary" "$config_path"
  if ! ln -s "$config_path" "$enabled_path" || ! nginx -t; then
    rm -f "$enabled_path" "$config_path"
    log_error 'ACME 临时站点检查失败；已清理本站配置'
    return 41
  fi
  if ! systemctl reload nginx; then
    rm -f "$enabled_path" "$config_path"
    log_error 'Nginx reload 失败；已清理本站 ACME 配置'
    return 41
  fi
}

provision_nginx_site() {
  local domain="$1" config_path="$2" static_root="$3" email="$4"
  local enabled_path="${NGINX_SITES_ENABLED:-/etc/nginx/sites-enabled}/neon-tavern-user-$domain"
  if [[ ! -e "$config_path" && ! -L "$config_path" ]]; then
    install_nginx_challenge_site "$domain" "$config_path" || return $?
    if ! obtain_certificate "$domain" "${NEON_ACME_WEBROOT:-/var/www/neon-tavern-user-acme}" "$email"; then
      rm -f "$enabled_path" "$config_path"
      if nginx -t; then systemctl reload nginx || log_error '清理後 Nginx reload 失敗，請人工檢查'; fi
      return 42
    fi
    NEON_ALLOW_CONFIG_UPGRADE=1 install_nginx_site "$domain" "$config_path" "$static_root" || return $?
  else
    [[ -f "$config_path" && ! -L "$config_path" ]] || { log_error '本站配置不是普通文件'; return 40; }
    grep -Fq '# Managed by Neon Tavern user-edition installer.' "$config_path" || {
      log_error '本站已有非安裝器配置，拒絕更新'; return 40;
    }
    [[ -f "/etc/letsencrypt/live/$domain/fullchain.pem" ]] || {
      log_error '本站配置已存在但缺少有效證書，需人工核查後再修復'; return 40;
    }
    NEON_ALLOW_CONFIG_UPGRADE=1 install_nginx_site "$domain" "$config_path" "$static_root" || return $?
    obtain_certificate "$domain" "${NEON_ACME_WEBROOT:-/var/www/neon-tavern-user-acme}" "$email" || return $?
  fi
  install_certificate_hook "$domain"
}

install_certificate_hook() {
  local domain="$1" hook_dir="${NEON_CERTBOT_HOOK_DIR:-/etc/letsencrypt/renewal-hooks/deploy}"
  local hook="$hook_dir/neon-tavern-user-$domain" temporary
  mkdir -p "$hook_dir"
  if [[ -e "$hook" ]]; then
    [[ -f "$hook" && ! -L "$hook" ]] || { log_error '本站证书 hook 类型未知，拒绝覆盖'; return 43; }
    grep -Fq '# Managed by Neon Tavern user-edition installer.' "$hook" && return 0
    log_error '本站证书续期 hook 已被其他内容占用'; return 43
  fi
  temporary="$(mktemp "$hook_dir/.neon-tavern-user.XXXXXX")" || return 43
  cat > "$temporary" <<HOOK
#!/usr/bin/env bash
# Managed by Neon Tavern user-edition installer.
set -Eeuo pipefail
[[ "\${RENEWED_LINEAGE:-}" == "/etc/letsencrypt/live/$domain" ]] || exit 0
nginx -t
systemctl reload nginx
HOOK
  chmod 0750 "$temporary"
  mv "$temporary" "$hook"
}
