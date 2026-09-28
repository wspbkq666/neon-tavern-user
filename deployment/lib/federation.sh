#!/usr/bin/env bash

_install_exact_unit() {
  local source_file="$1" target_file="$2" temporary
  if [[ -e "$target_file" || -L "$target_file" ]]; then
    [[ -f "$target_file" && ! -L "$target_file" ]] || { log_error "systemd 路径类型未知：$target_file"; return 50; }
    cmp -s "$source_file" "$target_file" && return 0
    log_error "systemd 单元已存在且内容不同：$target_file；拒绝覆盖"
    return 50
  fi
  temporary="$(mktemp "$(dirname "$target_file")/.neon-tavern-user.XXXXXX")" || return 50
  cp "$source_file" "$temporary"
  chmod 0644 "$temporary"
  mv "$temporary" "$target_file"
}

install_federation_units() {
  local root="${1:-/}" systemd_root credential_dir
  systemd_root="$root/etc/systemd/system"
  credential_dir="$root/etc/neon-tavern-user/credentials"
  local dropin_dir="$systemd_root/neon-tavern-user-web.service.d"
  local dropin="$dropin_dir/credentials.conf" temp source_dir="$INSTALLER_DIR/systemd"
  local unit
  for unit in federation-private-key-154.pem federation-private-key-123.pem; do
    [[ -f "$credential_dir/$unit" ]] || { log_error "缺少凭据文件：$unit"; return 50; }
  done
  for unit in neon-tavern-user-web.service neon-tavern-user-worker.service neon-tavern-user-chat-sync.service neon-tavern-user-chat-sync.timer neon-tavern-user-site-command-poll.service neon-tavern-user-site-command-poll.timer; do
    if [[ -e "$systemd_root/$unit" || -L "$systemd_root/$unit" ]]; then
      [[ -f "$systemd_root/$unit" && ! -L "$systemd_root/$unit" && -f "$source_dir/$unit" ]] || {
        log_error "systemd 单元路径类型未知：$unit"; return 50;
      }
      cmp -s "$source_dir/$unit" "$systemd_root/$unit" || {
        log_error "systemd 单元内容与本安装器不一致：$unit"; return 50;
      }
    fi
  done
  if [[ -e "$dropin_dir" && ! -d "$dropin_dir" ]] || [[ -e "$dropin" && ( ! -f "$dropin" || -L "$dropin" ) ]]; then
    log_error 'Web credential drop-in 路径类型未知'; return 50
  fi
  if [[ -f "$dropin" ]] && ! grep -Fq '# Managed by Neon Tavern user-edition installer.' "$dropin"; then
    log_error 'Web credential drop-in 已被其他内容占用'; return 50
  fi
  mkdir -p "$systemd_root" "$dropin_dir"
  for unit in neon-tavern-user-web.service neon-tavern-user-worker.service neon-tavern-user-chat-sync.service neon-tavern-user-chat-sync.timer neon-tavern-user-site-command-poll.service neon-tavern-user-site-command-poll.timer; do
    [[ -f "$source_dir/$unit" ]] || { log_error "仓库缺少 systemd 模板：$unit"; return 50; }
    _install_exact_unit "$source_dir/$unit" "$systemd_root/$unit" || return $?
  done

  if [[ -e "$dropin" || -L "$dropin" ]]; then
    [[ -f "$dropin" && ! -L "$dropin" ]] || { log_error 'Web credential drop-in 类型未知'; return 50; }
    if ! grep -Fq '# Managed by Neon Tavern user-edition installer.' "$dropin"; then
      log_error 'Web credential drop-in 已被其他内容占用'; return 50
    fi
  fi
  temp="$(mktemp "$dropin_dir/.credentials.XXXXXX")" || return 50
  cat > "$temp" <<'CREDENTIALS'
# Managed by Neon Tavern user-edition installer.
[Service]
LoadCredential=federation-private-key-154:/etc/neon-tavern-user/credentials/federation-private-key-154.pem
LoadCredential=federation-private-key-123:/etc/neon-tavern-user/credentials/federation-private-key-123.pem
Environment=TAVERN_FEDERATION_PRIVATE_KEY_FILE=%d/federation-private-key-154
Environment=TAVERN_FEDERATION_MAIN_123_PRIVATE_KEY_FILE=%d/federation-private-key-123
CREDENTIALS
  for unit in 154 123; do
    if [[ -f "$credential_dir/federation-pairing-code-$unit" ]]; then
      local target_key="MAIN_154"
      [[ "$unit" == 154 ]] || target_key="MAIN_123"
      printf 'LoadCredential=federation-pairing-code-%s:/etc/neon-tavern-user/credentials/federation-pairing-code-%s\n' "$unit" "$unit" >> "$temp"
      printf 'Environment=TAVERN_FEDERATION_PAIRING_CODE_%s_FILE=%%d/federation-pairing-code-%s\n' "$target_key" "$unit" >> "$temp"
    fi
  done
  chmod 0644 "$temp"
  if [[ -f "$dropin" ]] && cmp -s "$temp" "$dropin"; then rm -f "$temp"; else mv "$temp" "$dropin"; fi
  systemctl daemon-reload || { log_error 'systemd daemon-reload 失败'; return 51; }
}

verify_dual_registration() {
  local base_url="$1" body
  [[ "$base_url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] || {
    log_error '健康检查地址必须是 HTTPS 站点根地址'; return 52;
  }
  body="$(curl --silent --show-error --fail --max-time 10 --max-redirs 0 --proto '=https' "$base_url/api/health/")" || {
    log_error '本站健康接口不可用，或双总站登记仍未完成'; return 52;
  }
  printf '%s' "$body" | python3 -c 'import json,sys; data=json.load(sys.stdin); sys.exit(0 if data.get("ok") is True and data.get("registered") is True and data.get("federation") == "registered" else 1)' || {
    log_error '健康接口未确认 154 与 123 均已登记'; return 52;
  }
}

_write_web_credentials_without_pairing() {
  local root="$1" systemd_root dropin temporary
  systemd_root="$root/etc/systemd/system"
  dropin="$systemd_root/neon-tavern-user-web.service.d/credentials.conf"
  temporary="$(mktemp "$(dirname "$dropin")/.credentials.XXXXXX")" || return 53
  cat > "$temporary" <<'CREDENTIALS'
# Managed by Neon Tavern user-edition installer.
[Service]
LoadCredential=federation-private-key-154:/etc/neon-tavern-user/credentials/federation-private-key-154.pem
LoadCredential=federation-private-key-123:/etc/neon-tavern-user/credentials/federation-private-key-123.pem
Environment=TAVERN_FEDERATION_PRIVATE_KEY_FILE=%d/federation-private-key-154
Environment=TAVERN_FEDERATION_MAIN_123_PRIVATE_KEY_FILE=%d/federation-private-key-123
CREDENTIALS
  chmod 0644 "$temporary"
  mv "$temporary" "$dropin"
}

finalize_federation() {
  local root="$1" base_url="$2" credential_dir
  credential_dir="$root/etc/neon-tavern-user/credentials"
  verify_dual_registration "$base_url" || return $?
  _write_web_credentials_without_pairing "$root" || return $?
  rm -f "$credential_dir/federation-pairing-code-154" "$credential_dir/federation-pairing-code-123"
  systemctl daemon-reload || { log_error '清除配对凭据后的 systemd daemon-reload 失败'; return 53; }
}
