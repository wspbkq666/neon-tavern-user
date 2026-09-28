#!/usr/bin/env bash

_installer_paths() {
  NEON_APP_ROOT="${NEON_APP_ROOT:-/opt/neon-tavern-user}"
  NEON_RELEASE_ROOT="${NEON_RELEASE_ROOT:-$NEON_APP_ROOT/releases}"
  NEON_CURRENT_LINK="${NEON_CURRENT_LINK:-$NEON_APP_ROOT/current}"
  NEON_DATA_ROOT="${NEON_DATA_ROOT:-/var/lib/neon-tavern-user}"
  NEON_ENV_FILE="${NEON_ENV_FILE:-/etc/neon-tavern-user/environment}"
  NEON_DATABASE_PATH="${NEON_DATABASE_PATH:-$NEON_DATA_ROOT/database.sqlite3}"
  NEON_BACKUP_ROOT="${NEON_BACKUP_ROOT:-/var/backups/neon-tavern-user}"
  NEON_INSTALL_LOCK="${NEON_INSTALL_LOCK:-/run/lock/neon-tavern-user-install.lock}"
}

create_install_backup() {
  local backup_dir="$1" temporary_db
  mkdir -p "$backup_dir" && chmod 0700 "$backup_dir" || return 60
  if [[ -f "$NEON_ENV_FILE" ]]; then
    install -m 0600 "$NEON_ENV_FILE" "$backup_dir/environment" || return 60
  fi
  if [[ -e "$NEON_DATABASE_PATH" ]]; then
    [[ -f "$NEON_DATABASE_PATH" && ! -L "$NEON_DATABASE_PATH" ]] || {
      log_error '数据库路径不是普通文件，拒绝备份'; return 60;
    }
    temporary_db="$backup_dir/.database.sqlite3.tmp"
    python3 - "$NEON_DATABASE_PATH" "$temporary_db" <<'PY' || {
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
destination = sqlite3.connect(sys.argv[2], timeout=30)
try:
    source.backup(destination)
    result = destination.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError("backup integrity check failed")
finally:
    destination.close()
    source.close()
PY
      rm -f "$temporary_db"
      log_error 'SQLite 一致性备份或完整性检查失败'
      return 60
    }
    chmod 0600 "$temporary_db" || return 60
    mv "$temporary_db" "$backup_dir/database.sqlite3" || return 60
  fi
  chmod 0600 "$backup_dir"/* 2>/dev/null || true
}

restore_database_backup() {
  local backup_file="$1" restore_tmp="$NEON_DATABASE_PATH.restore"
  [[ -f "$backup_file" ]] || return 61
  rm -f "$NEON_DATABASE_PATH-wal" "$NEON_DATABASE_PATH-shm"
  cp "$backup_file" "$restore_tmp" || return 61
  chown "${NEON_SERVICE_USER:-www-data}:${NEON_SERVICE_GROUP:-www-data}" "$restore_tmp" || return 61
  chmod 0640 "$restore_tmp"
  mv -f "$restore_tmp" "$NEON_DATABASE_PATH"
}

_run_release_migrations() {
  local release="$1" python_bin="$1/venv/bin/python"
  (
    set -a
    # shellcheck disable=SC1090
    . "$NEON_ENV_FILE"
    set +a
    cd "$release" || exit 1
    runuser --preserve-environment -u "${NEON_SERVICE_USER:-www-data}" -- "$python_bin" manage.py migrate --noinput
  )
}

_switch_current_release() {
  local release="$1" temporary="$NEON_CURRENT_LINK.new.$$"
  rm -f "$temporary"
  ln -s "$release" "$temporary" || return 62
  mv -Tf "$temporary" "$NEON_CURRENT_LINK"
}

_start_installed_services() {
  systemctl start neon-tavern-user-web.service || return 63
  finalize_federation "${NEON_FEDERATION_ROOT:-/}" "${NEON_HEALTH_URL:?缺少本站 HTTPS 地址}" || return 63
  systemctl start neon-tavern-user-worker.service || return 63
  systemctl enable --now neon-tavern-user-chat-sync.timer neon-tavern-user-site-command-poll.timer || return 63
}

_wait_for_installed_health() {
  local attempt max_attempts="${NEON_HEALTH_ATTEMPTS:-30}"
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    if verify_dual_registration "$NEON_HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  log_error '健康检查超时；新 release 和数据库备份均已保留'
  return 64
}

_upgrade_current_locked() {
  local release_sha="$1" release backup_dir previous_current had_database=0
  _installer_paths
  [[ "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]] || { log_error '升级版本必须是完整的 commit SHA'; return 60; }
  release="$NEON_RELEASE_ROOT/$release_sha"
  [[ -f "$release/manage.py" && -x "$release/venv/bin/python" ]] || {
    log_error '目标 release 尚未准备完成'; return 60;
  }
  mkdir -p "$NEON_DATA_ROOT" "$NEON_BACKUP_ROOT" "$NEON_RELEASE_ROOT"
  chmod 0700 "$NEON_BACKUP_ROOT"
  backup_dir="$NEON_BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%S%NZ)-$release_sha"
  [[ ! -e "$backup_dir" ]] || { log_error '同名备份目录已存在，拒绝覆盖'; return 60; }
  [[ -e "$NEON_DATABASE_PATH" ]] && had_database=1
  create_install_backup "$backup_dir" || { log_error '一致性备份失败；未停止服务'; return 60; }
  previous_current=''
  if [[ -L "$NEON_CURRENT_LINK" ]]; then
    previous_current="$(readlink -f "$NEON_CURRENT_LINK")"
  elif [[ -e "$NEON_CURRENT_LINK" ]]; then
    log_error 'current 路径不是安装器管理的符号链接；拒绝覆盖'; return 60
  fi

  if [[ -n "$previous_current" ]]; then
    systemctl stop neon-tavern-user-worker.service || { log_error '停止本站 worker 失败；未继续升级'; return 60; }
    systemctl stop neon-tavern-user-web.service || {
      systemctl start neon-tavern-user-worker.service || true
      log_error '停止本站 Web 服务失败；未继续升级'; return 60;
    }
  fi
  if ! _run_release_migrations "$release"; then
    if [[ "$had_database" == 1 ]] && ! restore_database_backup "$backup_dir/database.sqlite3"; then
      log_error '数据库恢复失败；本站服务保持停止，备份仍保留，请勿启动业务服务'
      return 62
    fi
    if [[ -n "$previous_current" ]]; then
      systemctl start neon-tavern-user-web.service || true
      systemctl start neon-tavern-user-worker.service || true
    fi
    log_error '数据库迁移失败；current 未切换，已保留 release 与备份'
    return 62
  fi
  _switch_current_release "$release" || {
    if [[ -n "$previous_current" ]]; then
      systemctl start neon-tavern-user-web.service || true
      systemctl start neon-tavern-user-worker.service || true
    fi
    log_error 'current 原子切换失败；原版本链接保持不变'; return 62;
  }
  if ! systemctl start neon-tavern-user-web.service; then
    systemctl stop neon-tavern-user-web.service neon-tavern-user-worker.service || true
    log_error '本站 Web/登记前置检查启动失败；新 release 与备份保留，未回滚数据库'
    return 63
  fi
  if ! _wait_for_installed_health; then
    systemctl stop neon-tavern-user-web.service neon-tavern-user-worker.service || true
    log_error '本站健康检查失败；为避免旧代码使用新数据库，保留当前 release 和备份供诊断'
    return 64
  fi
  finalize_federation "${NEON_FEDERATION_ROOT:-/}" "$NEON_HEALTH_URL" || return $?
  systemctl start neon-tavern-user-worker.service || { log_error '本站 worker 启动失败'; return 63; }
  systemctl enable --now neon-tavern-user-chat-sync.timer neon-tavern-user-site-command-poll.timer || {
    log_error '本站双总站 timer 启用失败'; return 63;
  }
  log_info "部署成功，版本 $release_sha；数据库备份：$backup_dir"
}

upgrade_current() {
  local release_sha="$1" lock_fd result
  _installer_paths
  mkdir -p "$(dirname "$NEON_INSTALL_LOCK")" || return 60
  exec {lock_fd}>"$NEON_INSTALL_LOCK"
  if ! flock -n "$lock_fd"; then
    exec {lock_fd}>&-
    log_error '已有另一项安装/升级正在运行'
    return 60
  fi
  if _upgrade_current_locked "$release_sha"; then result=0; else result=$?; fi
  flock -u "$lock_fd" || true
  exec {lock_fd}>&-
  return "$result"
}
