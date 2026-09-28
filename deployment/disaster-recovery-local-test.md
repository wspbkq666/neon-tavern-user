# 双站容灾本地演练

本演练只使用测试框架建立的临时目录、合成 SQLite 数据、随机测试密钥和本地 witness 测试数据库。它不读取开发数据库，不连接、不探测或修改 154/123，也不安装 systemd、证书、代理或防火墙规则。

## 一键验证

在仓库的 `backend` 目录执行：

```powershell
$env:TAVERN_DEBUG = '1'
& '.venv\Scripts\python.exe' manage.py test core.test_disaster_recovery_integration core.test_disaster_recovery_snapshot core.test_disaster_recovery_transfer core.test_disaster_recovery_failover core.test_disaster_recovery_writes core.test_disaster_recovery core.test_auth_security core.test_pages
Push-Location witness
$env:WITNESS_DEBUG = '1'
& '..\.venv\Scripts\python.exe' manage.py test witness_core
Pop-Location
node --test frontend_tests\disaster-recovery-ui.test.mjs frontend_tests\registration-consent.test.mjs
```

项目全量验收另需执行 `manage.py check`、`manage.py makemigrations --check --dry-run`、`manage.py test core` 和 `node --test frontend_tests/*.test.mjs`。

## 演练覆盖

- Witness 的 154 初始租约、123 等待租约失效后取得更高 epoch、签名与重放校验、并发单写者和 controlled handoff。
- Witness 中断或租约失效时节点失败关闭；旧 epoch 写入被拒绝；SQLite 事务提交前租约到期时回滚。
- 临时 154 生成签名 AES-GCM 快照，临时 123 校验 SQLite 完整性和媒体 SHA-256 后安装；篡改包不得替换已有有效副本。
- 端到端生命周期以本地内存 witness 驱动实际接管/回切函数：154 初始主站 -> 租约失效 -> 123 提升并写入 -> 154 只读 -> 123 快照回同步 -> witness 受控交接 -> 154 恢复主站；确认新记录跨回切保留且旧主不能继续写入。Witness HTTP 签名、并发和防重放协议另由 witness API 测试直接覆盖。
- 浏览器只在 witness 明确确认更高 epoch 和新 holder 后切换；单点超时及 witness 不可用均不误切。
- 未接受当前隐私告知的账号会阻止复制；界面要求重新确认现行告知。

## 结果解释

通过表示代码在合成数据和本地测试进程中满足这些断言，不证明线上主机时钟、磁盘、网络、证书、反向代理、systemd 权限或数据库状态正确。生产试运行必须另行授权，先对两站只读盘点，准备并验证备份，再按独立变更单执行；本地测试不能替代该步骤。
