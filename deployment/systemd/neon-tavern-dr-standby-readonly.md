# 154/123 热备只读运行说明

两个站点都启用 `TAVERN_DR_ENABLED=1`，并使用同一个 witness 地址、同一版本应用和相同的快照加密密钥。每台站点配置自己的 `TAVERN_NODE_ID`、Ed25519 私钥；互相登记对方公钥及 witness 公钥。凭据文件仅 root 可读，不能放进代码仓库或普通备份。

两台站点均设置：

```ini
TAVERN_DR_USE_REPLICA_ACTIVE=1
TAVERN_DR_REPLICA_ROOT=/var/lib/neon-tavern/replica
TAVERN_DR_ROLE_PATH=/var/lib/neon-tavern/dr-role.json
TAVERN_DR_LEASE_PATH=/var/lib/neon-tavern/dr-lease.json
TAVERN_DR_SNAPSHOT_LOCK_PATH=/var/lib/neon-tavern/dr-snapshot.lock
TAVERN_DR_STATUS_PATH=/var/lib/neon-tavern/dr-status.json
TAVERN_DR_SNAPSHOT_DIR=/var/lib/neon-tavern/snapshots
TAVERN_DR_READ_ONLY=1
```

首次初始化由运维人员把当前主站数据库和媒体制作成经过签名加密的快照，并在目标 `/var/lib/neon-tavern/replica` 下安装为 `active`。确认副本校验通过后，在原主站配置 `TAVERN_DR_READ_ONLY=0` 并启动轮询服务；新站点始终保持只读，直到 witness 签发更高 epoch 且本地 `active` 快照通过签名、版本、摘要和完整性校验。热备数据库和媒体路径直接指向 `replica/active`；每次目录切换都会递增角色 generation，各 Web worker 在下一次数据库访问时关闭旧连接并重连。

主站的 `TAVERN_DR_PEER_URL` 必须是备用站点的 HTTPS 根地址；每小时 timer 生成快照并发送。接收接口只允许只读节点，要求节点请求签名和包内独立签名，TLS 证书必须正常验证。同步状态接口报告最后成功时间；滞后超过一小时会显示告警。网络失败保留上一份有效副本和本地加密快照。

安装并启用 `neon-tavern-dr-lease-poll.timer` 与 `neon-tavern-dr-snapshot.timer`。前者每 15 秒检查/续租；witness 不可达、签名异常或租约失效时应用切为只读。首次启动不得直接在备用机执行迁移或连接主站数据库；先安装有效快照。

故障转移不承诺 IP 冷启动无感：新访客仍需访问备用地址，现有浏览器会按 witness 状态导航并要求重新登录。恢复原主后，在当前主站执行：

```sh
python manage.py prepare_dr_failback 154 --destination-url https://恢复站点地址
```

该命令先冻结当前主站写入，再把最新快照传至恢复站并请求 witness 签发新 epoch。失败时保持只读；轮询服务根据 witness 最新状态恢复安全角色。确认新主站状态接口及登录/只读验收后，才调整 DNS 或入口流量。

Witness 必须部署在独立故障域。若 witness 与两个业务节点同机，单机故障时无法仲裁，不能自动接管。首次上线前应在本地合成数据环境演练“154 故障、123 接管、154 恢复、受控回切”，并验证备份可恢复；本说明不执行任何线上服务器变更。
