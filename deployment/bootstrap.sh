#!/usr/bin/env bash
set -Eeuo pipefail

log_error() { printf '错误：%s\n' "$*" >&2; }

repo_url="${NEON_TAVERN_USER_REPO_URL:-}"
release_sha="${NEON_TAVERN_RELEASE_SHA:-}"
if [[ -z "$repo_url" ]]; then read -r -p '公开用户版 GitHub 仓库 HTTPS 地址：' repo_url; fi
if [[ -z "$release_sha" ]]; then read -r -p '要安装的完整 40 位 release commit SHA：' release_sha; fi

[[ "${EUID:-$(id -u)}" == 0 ]] || { log_error '请通过 sudo 以 root 运行'; exit 2; }
[[ "$repo_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] || {
  log_error '仓库必须是 GitHub HTTPS 用户版仓库地址'; exit 2;
}
[[ "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]] || {
  log_error '版本必须使用完整的 40 位 commit SHA'; exit 2;
}
command -v git >/dev/null 2>&1 || { log_error '服务器缺少 Git；请先安装 Git 后重新运行'; exit 127; }

workdir="$(mktemp -d /tmp/neon-tavern-user-bootstrap.XXXXXX)"
trap 'rm -rf "$workdir"' EXIT
checkout="$workdir/source"
git clone --no-checkout --no-tags "$repo_url" "$checkout" >/dev/null || {
  log_error '无法读取 GitHub 用户版仓库'; exit 3;
}
git -C "$checkout" checkout --detach "$release_sha" >/dev/null 2>&1 || {
  log_error '无法检出指定固定版本'; exit 3;
}
actual_sha="$(git -C "$checkout" rev-parse HEAD)"
[[ "${actual_sha,,}" == "${release_sha,,}" ]] || { log_error '检出的 commit SHA 不匹配'; exit 3; }
[[ -f "$checkout/deployment/install.sh" && -f "$checkout/deployment/lib/common.sh" ]] || {
  log_error '此版本缺少一键安装所需文件'; exit 3;
}

export NEON_TAVERN_USER_REPO_URL="$repo_url"
export NEON_TAVERN_RELEASE_SHA="$release_sha"
bash "$checkout/deployment/install.sh"
