#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
shopt -s nullglob
tests=("$TEST_DIR"/install-*.bash)
[[ ! -f "$TEST_DIR/user-release-boundary.bash" ]] || tests+=("$TEST_DIR/user-release-boundary.bash")
if ((${#tests[@]} == 0)); then
  printf '错误：没有找到安装器测试\n' >&2
  exit 1
fi

for test_script in "${tests[@]}"; do
  printf '\n==> %s\n' "${test_script##*/}"
  bash "$test_script"
done
