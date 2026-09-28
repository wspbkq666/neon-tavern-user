#!/usr/bin/env bash
set -Eeuo pipefail

INSTALLER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deployment/lib/common.sh
. "$INSTALLER_DIR/lib/common.sh"
# shellcheck source=deployment/lib/preflight.sh
. "$INSTALLER_DIR/lib/preflight.sh"
# shellcheck source=deployment/lib/config.sh
. "$INSTALLER_DIR/lib/config.sh"
# shellcheck source=deployment/lib/release.sh
. "$INSTALLER_DIR/lib/release.sh"
# shellcheck source=deployment/lib/web-server.sh
. "$INSTALLER_DIR/lib/web-server.sh"
# shellcheck source=deployment/lib/federation.sh
. "$INSTALLER_DIR/lib/federation.sh"
# shellcheck source=deployment/lib/upgrade.sh
. "$INSTALLER_DIR/lib/upgrade.sh"

main() {
  local config_root="${NEON_CONFIG_ROOT:-/etc/neon-tavern-user}"
  local repo_url="${NEON_TAVERN_USER_REPO_URL:-}" release_sha="${NEON_TAVERN_RELEASE_SHA:-}"
  local domain site_name email release destination confirm login_status
  [[ "${EUID:-$(id -u)}" == 0 ]] || { log_error '请以 root 运行此安装器（例如 sudo bash deployment/install.sh）'; return 2; }
  require_command apt-get || return $?

  if [[ -e "$config_root" ]]; then
    [[ -d "$config_root" && ! -L "$config_root" && -f "$config_root/.managed-by-neon-tavern-user" && ! -L "$config_root/.managed-by-neon-tavern-user" && -f "$config_root/environment" && ! -L "$config_root/environment" ]] || {
      log_error "配置目录已存在但无法确认归属：$config_root"; return 2;
    }
    [[ "$(stat -c '%u:%a' "$config_root")" == '0:700' && "$(stat -c '%u:%a' "$config_root/environment")" == '0:600' ]] || {
      log_error '现有配置目录/环境文件权限不安全；拒绝以 root 读取'; return 2;
    }
    set -a
    # This environment file is root-owned and created by the installer.
    # shellcheck disable=SC1091
    . "$config_root/environment"
    set +a
    [[ "${TAVERN_FEDERATION_ROLE:-}" == satellite ]] || { log_error '本站不是强制 satellite 配置，拒绝升级'; return 2; }
    [[ "${TAVERN_FEDERATION_CENTRAL_URL:-}" == https://154.222.26.47 ]] || { log_error '154 总站地址不匹配'; return 2; }
    [[ "${TAVERN_FEDERATION_SITE_URL:-}" =~ ^https://[A-Za-z0-9.-]+$ ]] || { log_error '本站现有 HTTPS 域名配置无效'; return 2; }
    domain="${TAVERN_FEDERATION_SITE_URL#https://}"
    site_name="${TAVERN_FEDERATION_SITE_NAME:-霓虹酒馆分站}"
    email="${TAVERN_CERTBOT_EMAIL:-}"
    for credential in "$config_root"/credentials/federation-private-key-154.pem "$config_root"/credentials/federation-private-key-123.pem; do
      [[ -f "$credential" && ! -L "$credential" && "$(stat -c '%u:%a' "$credential")" == '0:600' ]] || {
        log_error '现有总站私钥缺失或权限不是 root-only；拒绝升级'; return 2;
      }
    done
    NEON_UPGRADE_MODE=1
    export NEON_UPGRADE_MODE
    log_info "检测到本站已有受管配置，将保留用户数据并安全升级：$domain"
  else
    prompt_install_config || return $?
    domain="$INSTALL_DOMAIN"
    site_name="$INSTALL_SITE_NAME"
    email="$INSTALL_EMAIL"
  fi

  run_preflight "$domain" || return $?
  if [[ -z "$repo_url" ]]; then read -r -p '用户版 GitHub 仓库 HTTPS 地址：' repo_url; fi
  if [[ -z "$release_sha" ]]; then read -r -p '要安装的完整 40 位 release commit SHA：' release_sha; fi
  [[ "$repo_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] || {
    log_error '安装源必须是 GitHub HTTPS 用户版仓库'; return 2;
  }
  [[ "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]] || { log_error '版本必须填写完整的 40 位 commit SHA'; return 2; }

  printf '\n将执行以下本站操作：\n'
  printf '域名：%s\n站点：%s\n版本：%s\n' "$domain" "$site_name" "$release_sha"
  printf '新增/更新本站专属 Nginx 配置；可能安装 Nginx、Certbot 与 Python 编译依赖。\n'
  printf '服务：neon-tavern-user-web/worker；数据：/var/lib/neon-tavern-user；备份：/var/backups/neon-tavern-user。\n'
  printf '只对本站服务执行升级操作；不会改写其他站点配置或停止无关服务。\n'
  read -r -p '确认开始请输入“安装”：' confirm
  [[ "$confirm" == 安装 ]] || { log_info '已取消；尚未写入系统配置'; return 0; }

  export DEBIAN_FRONTEND=noninteractive
  apt-get update || { log_error 'apt 软件索引更新失败'; return 3; }
  apt-get install --yes --no-upgrade git openssl nginx certbot python3 python3-venv \
    build-essential libffi-dev libjpeg-dev zlib1g-dev || {
      log_error '安装系统依赖失败；请查看 apt 输出后重试'; return 3;
    }

  if [[ "${NEON_UPGRADE_MODE:-0}" != 1 ]]; then
    create_instance_secrets / "$domain" "$site_name" || return $?
    chmod 0700 "$config_root"
    : > "$config_root/.managed-by-neon-tavern-user"
    chmod 0644 "$config_root/.managed-by-neon-tavern-user"
  fi

  NEON_APP_ROOT="${NEON_APP_ROOT:-/opt/neon-tavern-user}"
  NEON_DATA_ROOT="${NEON_DATA_ROOT:-/var/lib/neon-tavern-user}"
  NEON_BACKUP_ROOT="${NEON_BACKUP_ROOT:-/var/backups/neon-tavern-user}"
  NEON_RELEASE_ROOT="${NEON_RELEASE_ROOT:-$NEON_APP_ROOT/releases}"
  NEON_CURRENT_LINK="${NEON_CURRENT_LINK:-$NEON_APP_ROOT/current}"
  NEON_ENV_FILE="$config_root/environment"
  NEON_DATABASE_PATH="$NEON_DATA_ROOT/database.sqlite3"
  NEON_HEALTH_URL="https://$domain"
  NEON_CONFIG_ROOT="$config_root"
  NEON_SERVICE_USER=www-data
  NEON_SERVICE_GROUP=www-data
  export NEON_APP_ROOT NEON_DATA_ROOT NEON_BACKUP_ROOT NEON_RELEASE_ROOT NEON_CURRENT_LINK
  export NEON_ENV_FILE NEON_DATABASE_PATH NEON_HEALTH_URL NEON_CONFIG_ROOT NEON_SERVICE_USER NEON_SERVICE_GROUP

  install -d -o root -g root -m 0755 "$NEON_APP_ROOT" "$NEON_RELEASE_ROOT"
  : > "$NEON_APP_ROOT/.managed-by-neon-tavern-user"
  chmod 0644 "$NEON_APP_ROOT/.managed-by-neon-tavern-user"
  if [[ "${NEON_UPGRADE_MODE:-0}" != 1 ]]; then
    install -d -o www-data -g www-data -m 0750 "$NEON_DATA_ROOT" "$NEON_DATA_ROOT/media"
    : > "$NEON_DATA_ROOT/.managed-by-neon-tavern-user"
    chmod 0644 "$NEON_DATA_ROOT/.managed-by-neon-tavern-user"
  else
    if ! runuser -u www-data -- test -w "$NEON_DATA_ROOT" || ! runuser -u www-data -- test -w "$NEON_DATA_ROOT/media"; then
      log_error '服务账号无法写入现有数据/媒体目录；为保护数据不自动改权限'; return 2;
    fi
  fi
  install -d -o root -g root -m 0700 "$NEON_BACKUP_ROOT"
  if [[ "${NEON_UPGRADE_MODE:-0}" != 1 ]]; then
    : > "$NEON_BACKUP_ROOT/.managed-by-neon-tavern-user"
    chmod 0600 "$NEON_BACKUP_ROOT/.managed-by-neon-tavern-user"
  fi
  install -d -o root -g root -m 0700 /run/lock

  destination="$NEON_RELEASE_ROOT/$release_sha"
  if [[ -z "$repo_url" ]]; then repo_url="$NEON_TAVERN_USER_REPO_URL"; fi
  NEON_TAVERN_USER_REPO_URL="$repo_url" fetch_release "$repo_url" "$release_sha" "$release_sha" "$destination" || return $?
  prepare_release "$destination" "$destination/venv" "$NEON_ENV_FILE" || return $?

  NGINX_SITES_AVAILABLE="${NGINX_SITES_AVAILABLE:-/etc/nginx/sites-available}"
  NGINX_SITES_ENABLED="${NGINX_SITES_ENABLED:-/etc/nginx/sites-enabled}"
  CONFIG_PATH="$NGINX_SITES_AVAILABLE/neon-tavern-user-$domain"
  NEON_STATIC_ROOT="$NEON_CURRENT_LINK/collected_static"
  NEON_ACME_WEBROOT="${NEON_ACME_WEBROOT:-/var/www/neon-tavern-user-acme}"
  NEON_ALLOW_CONFIG_UPGRADE="${NEON_UPGRADE_MODE:-0}"
  export NGINX_SITES_AVAILABLE NGINX_SITES_ENABLED NEON_STATIC_ROOT NEON_ACME_WEBROOT NEON_ALLOW_CONFIG_UPGRADE
  mkdir -p "$NGINX_SITES_AVAILABLE" "$NGINX_SITES_ENABLED" "$NEON_ACME_WEBROOT"
  provision_nginx_site "$domain" "$CONFIG_PATH" "$NEON_STATIC_ROOT" "$email" || return $?
  install_federation_units / || return $?
  upgrade_current "$release_sha" || return $?

  login_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 15 --max-redirs 0 --proto '=https' "$NEON_HEALTH_URL/login/")" || {
    log_error '健康接口通过，但登录页访问失败；请检查本站 Nginx 与服务日志'; return 4;
  }
  [[ "$login_status" == 200 ]] || { log_error "登录页返回 HTTP $login_status，不报告安装成功"; return 4; }
  printf '\n安装完成：https://%s/login/\n首次注册账号将成为本站网站管理员。\n数据目录：%s\n备份目录：%s\n' \
    "$domain" "$NEON_DATA_ROOT" "$NEON_BACKUP_ROOT"
}

if [[ "${NEON_TAVERN_INSTALLER_TEST:-0}" != 1 ]]; then
  main "$@"
fi
