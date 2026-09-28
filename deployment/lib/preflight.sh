#!/usr/bin/env bash

OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

check_platform() {
  local os_id version_id machine
  [[ -r "$OS_RELEASE_FILE" ]] || { log_error "无法读取系统信息：$OS_RELEASE_FILE"; return 10; }
  # shellcheck disable=SC1090
  . "$OS_RELEASE_FILE"
  os_id="${ID:-}"
  version_id="${VERSION_ID:-}"
  machine="$(uname -m 2>/dev/null)" || { log_error '无法检测系统架构'; return 10; }

  [[ "$os_id" == ubuntu && ( "$version_id" == 22.04 || "$version_id" == 24.04 ) ]] || {
    log_error '仅支持 Ubuntu 22.04 或 24.04 LTS'; return 10;
  }
  [[ "$machine" == x86_64 ]] || { log_error '仅支持 x86_64 架构'; return 10; }
  command -v systemctl >/dev/null 2>&1 || { log_error '未找到 systemctl，当前系统未使用 systemd'; return 10; }
  systemctl is-system-running >/dev/null 2>&1 || {
    log_error 'systemd 未处于可用状态'; return 10;
  }
}

check_domain() {
  local domain="$1"
  [[ "$domain" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || {
    log_error '请输入有效的公网域名，不支持 IP 地址'; return 11;
  }
  command -v getent >/dev/null 2>&1 || { log_error '缺少 DNS 查询命令 getent'; return 11; }
  getent ahostsv4 "$domain" >/dev/null 2>&1 || {
    log_error "域名 $domain 当前没有可解析的 IPv4 A 记录"; return 11;
  }
}

check_ports() {
  local listeners line port process local_address main_pid
  command -v ss >/dev/null 2>&1 || { log_error '缺少端口查询命令 ss'; return 12; }
  listeners="$(ss -H -ltnp 2>/dev/null)" || { log_error '无法读取监听端口'; return 12; }
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    read -r _ _ _ local_address _ _ <<< "$line"
    port="${local_address##*:}"
    [[ "$port" == 80 || "$port" == 443 || "$port" == 18087 ]] || continue
    process="$line"
    if [[ "$port" == 18087 && "${NEON_UPGRADE_MODE:-0}" == 1 ]]; then
      if systemctl is-active --quiet neon-tavern-user-web.service >/dev/null 2>&1; then
        main_pid="$(systemctl show --property=MainPID --value neon-tavern-user-web.service 2>/dev/null)"
        if [[ "$main_pid" =~ ^[1-9][0-9]*$ && "$process" == *"pid=$main_pid,"* ]]; then continue; fi
      fi
    fi
    if [[ "$process" != *nginx* ]]; then
      if [[ "$port" == 18087 ]]; then
        log_error '霓虹酒馆专用后端端口 18087 已被占用；为保护已有项目，停止安装'
      else
        log_error "端口 $port 已被非 Nginx 服务占用；为保护已有项目，停止安装"
      fi
      return 12
    fi
  done <<< "$listeners"
}

check_nginx_conflicts() {
  local domain="$1" output line token
  command -v nginx >/dev/null 2>&1 || return 0
  if [[ "${NEON_UPGRADE_MODE:-0}" == 1 ]] && declare -F _nginx_domain_conflict >/dev/null; then
    local own_enabled="${NGINX_SITES_ENABLED:-/etc/nginx/sites-enabled}/neon-tavern-user-$domain"
    _nginx_domain_conflict "$domain" "$own_enabled" && {
      log_error "除本站配置以外，Nginx 已有其他站点占用域名 $domain"; return 13;
    }
    return 0
  fi
  output="$(nginx -T 2>&1)" || { log_error '现有 Nginx 配置检查失败；请先修复，不会修改配置'; return 13; }
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*server_name[[:space:]] ]] || continue
    line="${line#*server_name}"
    line="${line%%;*}"
    for token in $line; do
      if [[ "$token" == "$domain" || "$token" == "*.$domain" || "$token" == ".$domain" ]]; then
        log_error "Nginx 已有站点占用域名 $domain；不会覆盖现有站点"
        return 13
      fi
    done
  done <<< "$output"
}

check_web_stack() {
  local service
  for service in apache2 caddy; do
    if systemctl is-active --quiet "$service" >/dev/null 2>&1; then
      log_error "检测到正在运行的 $service；为避免争抢监听端口，停止安装"
      return 15
    fi
  done
  if command -v nginx >/dev/null 2>&1 && ! systemctl is-active --quiet nginx >/dev/null 2>&1; then
    log_error '检测到已安装但未运行的 Nginx；为避免擅自启动其现有站点，请先确认宿主机 Web 服务状态'
    return 15
  fi
}

check_install_paths() {
  local app_root="${NEON_APP_ROOT:-/opt/neon-tavern-user}"
  local data_root="${NEON_DATA_ROOT:-/var/lib/neon-tavern-user}"
  local backup_root="${NEON_BACKUP_ROOT:-/var/backups/neon-tavern-user}"
  local config_root="${NEON_CONFIG_ROOT:-/etc/neon-tavern-user}"
  if [[ -e "$config_root" && ! -f "$config_root/.managed-by-neon-tavern-user" ]]; then
    log_error '本站配置目录已存在但没有安装器标记；拒绝覆盖'
    return 16
  fi
  if [[ "${NEON_UPGRADE_MODE:-0}" == 1 ]]; then
    for path in "$app_root" "$data_root" "$backup_root"; do
      [[ ! -e "$path" || -f "$path/.managed-by-neon-tavern-user" ]] || {
        log_error "目标目录已存在但无法确认归属：$path"; return 16;
      }
    done
  else
    for path in "$app_root" "$data_root" "$backup_root"; do
      [[ ! -e "$path" ]] || { log_error "目标目录已存在；为保护原项目，停止安装：$path"; return 16; }
    done
  fi
}

check_existing_units() {
  local unit
  for unit in \
    neon-tavern-user-web.service \
    neon-tavern-user-worker.service \
    neon-tavern-user-chat-sync.service \
    neon-tavern-user-chat-sync.timer \
    neon-tavern-user-site-command-poll.service \
    neon-tavern-user-site-command-poll.timer; do
    if [[ -e "$SYSTEMD_DIR/$unit" || -L "$SYSTEMD_DIR/$unit" ]]; then
      if [[ "${NEON_UPGRADE_MODE:-0}" == 1 && -f "$INSTALLER_DIR/systemd/$unit" && -f "$SYSTEMD_DIR/$unit" && ! -L "$SYSTEMD_DIR/$unit" ]] &&
        cmp -s "$INSTALLER_DIR/systemd/$unit" "$SYSTEMD_DIR/$unit"; then
        continue
      fi
      log_error "发现同名 systemd 单元 $unit；无法确认其归属，停止安装"
      return 14
    fi
    if systemctl cat "$unit" >/dev/null 2>&1; then
      log_error "systemd 已加载同名单元 $unit，但磁盘归属不明确；停止安装"
      return 14
    fi
  done
  if [[ -e "$SYSTEMD_DIR/neon-tavern-user-web.service.d" ]]; then
    if [[ "${NEON_UPGRADE_MODE:-0}" == 1 && -f "$SYSTEMD_DIR/neon-tavern-user-web.service.d/credentials.conf" ]] &&
      grep -Fq '# Managed by Neon Tavern user-edition installer.' "$SYSTEMD_DIR/neon-tavern-user-web.service.d/credentials.conf"; then
      :
    else
      log_error '发现已有 Web 服务 drop-in 目录；无法确认其归属，停止安装'
      return 14
    fi
  fi
}

run_preflight() {
  local domain="${1:-}"
  [[ -n "$domain" ]] || { log_error '预检缺少域名'; return 2; }
  check_platform || return $?
  check_domain "$domain" || return $?
  check_ports || return $?
  check_web_stack || return $?
  check_nginx_conflicts "$domain" || return $?
  check_existing_units || return $?
  check_install_paths || return $?
  log_info '只读预检通过；尚未安装软件或修改系统'
}
