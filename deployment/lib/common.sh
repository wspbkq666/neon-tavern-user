#!/usr/bin/env bash

log_info() {
  printf '信息：%s\n' "$*"
}

log_error() {
  printf '错误：%s\n' "$*" >&2
}

die() {
  local message="$1"
  local code="${2:-1}"
  log_error "$message"
  return "$code"
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || die "缺少必要命令：$command_name" 127
}
