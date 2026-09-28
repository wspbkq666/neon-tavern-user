#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
fail() { printf '失败：%s\n' "$*" >&2; exit 1; }

grep -Fq 'TAVERN_FEDERATION_ROLE=satellite' "$ROOT/deployment/lib/config.sh" || fail '首次安装没有强制 satellite'
grep -Fq 'TAVERN_FEDERATION_CENTRAL_URL=https://154.222.26.47' "$ROOT/deployment/lib/config.sh" || fail '154 总站地址不是固定配置'
grep -Fq '"main_123": "https://123.56.125.209"' "$ROOT/core/site_federation_client.py" || fail '123 总站地址没有固定在应用端'
! grep -Fq 'TAVERN_FEDERATION_ROLE=central' "$ROOT/deployment/install.sh" || fail '安装器允许选择 central 角色'
grep -Fq '/var/lib/neon-tavern-user' "$ROOT/deployment/systemd/neon-tavern-user-web.service" || fail 'Web 服务没有隔离用户数据目录'
grep -Fq '/var/backups/neon-tavern-user' "$ROOT/deployment/lib/upgrade.sh" || fail '升级备份路径没有隔离'
grep -Fq 'git clone --no-checkout --no-tags' "$ROOT/deployment/bootstrap.sh" || fail 'bootstrap 未使用固定 Git checkout'
! grep -Eiq 'github\.com/.+/(admin|central)(\.git)?$' "$ROOT/deployment/README-user-install.md" 2>/dev/null || fail '用户安装文档指向管理员/总站仓库'

printf '通过：固定 satellite、双总站地址、数据隔离与固定 SHA 引导边界\n'
