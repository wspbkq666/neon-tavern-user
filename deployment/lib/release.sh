#!/usr/bin/env bash

verify_user_release_source() {
  local source_root="$1"
  [[ -f "$source_root/deployment/user-edition-release.json" ]] || {
    log_error '源码缺少用户版发行清单；管理员/总站仓库不能作为分站安装源'; return 31;
  }
  if ! python3 "$source_root/deployment/verify_user_edition.py" "$source_root" >/dev/null; then
    log_error '用户版源码仍包含全局管理员权限或总站管理接口'
    return 31
  fi
}

fetch_release() {
  local repo_url="$1" ref="$2" release_sha="$3" destination="$4" actual_sha temporary_release parent
  [[ "$repo_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] || {
    log_error '用户版源码地址必须是 GitHub HTTPS 仓库'; return 30;
  }
  [[ "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]] || {
    log_error '版本必须使用完整的 40 位 Git commit SHA'; return 30;
  }
  [[ "$ref" =~ ^[0-9a-fA-F]{40}$ || "$ref" =~ ^v[0-9][A-Za-z0-9.+_-]*$ ]] || {
    log_error '只接受完整 commit SHA 或以 v 开头的固定版本 tag'; return 30;
  }
  command -v git >/dev/null 2>&1 || { log_error '缺少 git'; return 127; }
  if [[ -e "$destination" ]]; then
    [[ -d "$destination/.git" ]] || { log_error 'release 目标目录已存在但不是 Git checkout'; return 30; }
    actual_sha="$(git -C "$destination" rev-parse HEAD 2>/dev/null)" || return 30
    [[ "${actual_sha,,}" == "${release_sha,,}" ]] || { log_error '现有 release 的 commit SHA 不匹配'; return 30; }
    [[ -z "$(git -C "$destination" status --porcelain 2>/dev/null)" ]] || {
      log_error '现有 release 存在本地改动；拒绝覆盖'; return 30;
    }
    verify_user_release_source "$destination" || return $?
    [[ -f "$destination/manage.py" && -f "$destination/requirements.txt" ]] || {
      log_error '现有 release 缺少用户版后端入口或依赖清单'; return 31;
    }
    return 0
  fi
  parent="$(dirname "$destination")"
  mkdir -p "$parent"
  [[ ! -e "$destination" && ! -L "$destination" ]] || { log_error 'release 目标路径已被占用'; return 30; }
  temporary_release="$(mktemp -d "$parent/.neon-tavern-user-release.XXXXXX")" || return 31
  git clone --no-checkout --no-tags "$repo_url" "$temporary_release" >/dev/null || {
    rm -rf "$temporary_release"
    log_error '无法检出用户版仓库'; return 31;
  }
  local checkout_ref="$ref"
  [[ "$ref" =~ ^v ]] && checkout_ref="refs/tags/$ref"
  git -C "$temporary_release" checkout --detach "$checkout_ref" >/dev/null 2>&1 || {
    rm -rf "$temporary_release"
    log_error '无法检出指定版本'; return 31;
  }
  actual_sha="$(git -C "$temporary_release" rev-parse HEAD)" || { rm -rf "$temporary_release"; return 31; }
  if [[ "${actual_sha,,}" != "${release_sha,,}" ]]; then
    rm -rf "$temporary_release"
    log_error '检出的 commit SHA 与已批准的 release SHA 不一致'
    return 31
  fi
  verify_user_release_source "$temporary_release" || { rm -rf "$temporary_release"; return 31; }
  [[ -f "$temporary_release/manage.py" && -f "$temporary_release/requirements.txt" ]] || {
    rm -rf "$temporary_release"
    log_error '仓库缺少用户版后端入口或依赖清单'; return 31;
  }
  mv -T "$temporary_release" "$destination" || {
    rm -rf "$temporary_release"
    log_error '无法原子安置经过验证的 release'; return 31;
  }
}

prepare_release() {
  local release_dir="$1" venv_dir="$2" env_file="$3" python_bin pip_bin
  [[ -f "$release_dir/manage.py" && -f "$release_dir/requirements.txt" ]] || {
    log_error 'release 缺少 Django 后端文件'; return 32;
  }
  [[ -r "$env_file" ]] || { log_error '无法读取本站环境配置'; return 32; }
  python_bin="${NEON_PYTHON_BIN:-python3}"
  command -v "$python_bin" >/dev/null 2>&1 || { log_error "缺少 Python 命令：$python_bin"; return 127; }
  if [[ ! -e "$venv_dir" ]]; then
    "$python_bin" -m venv "$venv_dir" || { log_error '创建 release 虚拟环境失败'; return 32; }
  elif [[ ! -x "$venv_dir/bin/python" || ! -x "$venv_dir/bin/pip" ]]; then
    log_error 'release 虚拟环境已存在但不完整；拒绝覆盖'
    return 32
  fi
  pip_bin="$venv_dir/bin/pip"
  python_bin="$venv_dir/bin/python"
  "$pip_bin" install --disable-pip-version-check -r "$release_dir/requirements.txt" || {
    log_error '安装 Python 依赖失败'; return 32;
  }
  (
    set -a
    # This file is created by the installer with restricted permissions and validated values.
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
    cd "$release_dir"
    "$python_bin" manage.py check || exit 1
    "$python_bin" manage.py collectstatic --noinput || exit 1
    runuser --preserve-environment -u "${NEON_SERVICE_USER:-www-data}" -- "$python_bin" manage.py showmigrations --plan >/dev/null
  ) || { log_error 'Django 配置、静态资源或迁移预检查失败'; return 32; }
}
