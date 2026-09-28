# 154 主站与 123 热备部署步骤

## 运行约定

- 154 是默认主站；123 是只读热备。Witness 的公网映射使用 `hbe3.wch1.top:43111 -> 内网 443`，业务站点继续使用各自原有端口。
- 154 保持原来的霓虹酒馆 `8088` HTTP 服务，不改占用 80 的 Agri Ledger，不新增用户入口端口。123 保留现有 HTTPS。
- 主站到 123 使用现有受信任 HTTPS。个人 HTTP 模式下，123 回传到 154 时仅允许 `TAVERN_DR_ALLOW_HTTP_PEER=1` 且目标 IP 在 `TAVERN_DR_HTTP_PEER_HOSTS` 中；快照文件本身仍使用 AES-GCM 加密并由 Ed25519 签名。此选项默认关闭，不能填域名或任意主机，也不会关闭 123 的浏览器 HTTPS。
- 154 上的浏览器可以在 123 经 Witness 确认接管后自动跳转；切到 HTTPS 的 123 后，浏览器不能用混合内容探测 HTTP 的 154，因此回切后需手动重新打开 154 地址。登录 Cookie 不跨 IP 共享，切换后需重新登录。
- 154 的登录、对话和模型请求仍是 HTTP 明文传输。只按本人使用的风险接受；不要把这个模式开放给其他用户或公共网络。

## 上线前

1. 从同一用户版 Git 提交构建 154 和 123，确认代码版本、数据库迁移及快照加密密钥版本相同。用户版不含全局管理员代码。
2. 只读核验两站当前霓虹酒馆目录、服务、数据库路径、媒体目录、监听端口及健康接口。不要动 154 的 80 端口、其他应用、123 的 Nginx 站点或 NapCat。
3. 分别为两站数据库生成 SQLite 一致性备份，并备份媒体目录和服务环境文件；验证备份可读、记录文件摘要。保留 123 原数据库和媒体，不把它们直接覆盖。
4. 先部署本版本并执行 `python manage.py migrate`。现有账号登录后须在隐私协议和用户规则页面明确确认异地复制；未确认前不得开启复制。
5. 准备两站各自的 Ed25519 节点私钥、对方公钥、Witness 公钥、同一份 32 字节快照加密密钥、同一份 `TAVERN_ENCRYPTION_KEY` 和 Witness CA 公共证书。私钥、加密密钥和 `.env` 只能由 root 读取，不提交 Git。

## 初始化 154 主站

1. 暂停 `neon-tavern-user.service` 和该站写入 worker。只停止霓虹酒馆自己的服务。
2. 在容灾关闭的环境确认 `TAVERN_DB_PATH`、`TAVERN_MEDIA_ROOT` 指向原数据，`TAVERN_DR_USE_REPLICA_ACTIVE=0`，并设置 `/var/lib/neon-tavern/replica` 为活动副本目录。
3. 运行：

   ```sh
   python manage.py initialize_dr_active --confirm-maintenance
   ```

   命令会使用 SQLite Backup API 一致性复制数据库、复制媒体并校验完整性；目标 `replica/active` 已存在时会拒绝覆盖。出错时原始数据库和媒体保持不变。
4. 配置 154 的 `TAVERN_DR_ENABLED=1`、`TAVERN_DR_USE_REPLICA_ACTIVE=1`、`TAVERN_DR_READ_ONLY=0`、`TAVERN_NODE_ID=154`、Witness 与签名密钥；确认活动副本存在，再启动本站服务。
5. **先只启动 154 的租约轮询**。Witness 为空时首个获准节点会取得初始租约；必须先确认 `/api/disaster-recovery/status/` 显示 `node_id=154`、`role=primary` 和有效 epoch，才启动 123。

## 初始化 123 热备

1. 先停止 123 的霓虹酒馆服务并保留其原数据库/媒体备份。不能对未知数据直接覆盖。
2. 配置相同版本、相同快照密钥和 Witness CA；设置 `TAVERN_NODE_ID=123`、`TAVERN_DR_READ_ONLY=1`、`TAVERN_DR_USE_REPLICA_ACTIVE=0`，使其尚未把空目录当作数据库启动。
3. 从 154 生成一个经签名加密的初始快照，安全传至 123，再在 123 运行：

   ```sh
   python manage.py receive_dr_snapshot /安全暂存路径/初始快照.drsnap
   ```

   快照需要匹配代码版本、数据库迁移、Witness/节点公钥和加密密钥。原有 123 数据仍保留在原路径及备份中；新的热备只使用 `replica/active`。
4. 123 的活动副本通过校验后，设置 `TAVERN_DR_ENABLED=1`、`TAVERN_DR_USE_REPLICA_ACTIVE=1`，并为 `TAVERN_DR_PEER_URL` 使用 `http://154.222.26.47:8088`。启用 HTTP 单节点白名单，且只把 `154.222.26.47` 放入 `TAVERN_DR_HTTP_PEER_HOSTS`；保留 123 自身的 HTTPS 设置。
5. 启动 123 服务和租约轮询，确认它显示 `role=read_only`、Witness holder 为 154。再在 154 配置 123 的 HTTPS `/api/disaster-recovery/replica/` 入口并启用每小时快照 timer。

## 验收与回退

- 两站健康接口均返回 200；154 状态为主、123 为只读；首份副本显示健康且滞后时间在一小时内。
- 合成测试确认从 154 同步到 123、只读副本拒绝写入、Witness 授予更高 epoch 后 123 才可写。不得通过直接停止 Agri Ledger、Nginx 或 NapCat 来制造故障。
- 真实切换演练前先确认两站备份可恢复、用户接受短暂停机和最多一小时的数据回退窗口。演练时只操作对应霓虹酒馆服务，并在每一步核对 Witness epoch 和状态 API。
- 154 恢复后保持只读，先从当前主站 123 安装最新快照，再在 123 执行 `python manage.py prepare_dr_failback 154 --destination-url http://154.222.26.47:8088`。该 HTTP 方向仅在上述个人模式白名单开启时有效。确认 154 获得更高 epoch、状态为主、123 回到只读后，再手动访问 154。
- 任一副本校验、密钥、TLS、服务身份或状态不匹配时停止自动提升，不删除原数据库、备份、租约记录或旧 release。恢复时仅回退霓虹酒馆自己的配置和代码。

线上演练尚未完成前，不得把“本地测试通过”描述为生产容灾已启用。
