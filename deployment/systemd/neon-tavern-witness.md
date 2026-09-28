# Witness 仲裁服务部署说明

Witness 只保存签名租约、epoch 和防重放 nonce，不保存业务账号或对话。它必须位于与 154、123 不同的故障域；同机部署无法仲裁该主机故障。

服务示例 `neon-tavern-witness.service.example` 在独立挂机宝上直接以私有 CA 叶证书提供 HTTPS，并监听内网 `443`。该主机不承载其他网站，不接入 154 的 Node 服务或 123 的 Nginx。公网只通过已由服务商确认的 NAT 高位端口转发到此内网端口；不得假定端口映射或为调试开放明文 HTTP。浏览器不直接请求 witness，只有 154/123 后端使用专用 CA bundle 查询状态。

`/etc/neon-tavern-witness.env` 至少需要单独生成并妥善保存以下值：

- `WITNESS_SECRET_KEY`：随机 Django 密钥。
- `WITNESS_SIGNING_PRIVATE_KEY`：独立 Ed25519 私钥；不得与任一业务节点密钥复用。
- `WITNESS_NODE_PUBLIC_KEYS`：仅登记 154、123 的节点公钥 JSON。
- `WITNESS_DB_PATH=/var/lib/neon-tavern-witness/witness.sqlite3`。
- `WITNESS_INITIAL_NODE=154` 和 `WITNESS_ALLOWED_HOSTS`。浏览器不直连 witness，`WITNESS_PUBLIC_STATUS_ORIGINS` 保持为空。
- `WITNESS_TLS_CERT`、`WITNESS_TLS_KEY`：证书及叶私钥文件路径，指向带 `DNS:hbe3.wch1.top` SAN 的叶证书。叶私钥仅 witness 服务账户可读。

凭据文件由 root 持有且权限为 `0600`；状态目录由 `neon-witness` 用户持有。部署前创建专用系统用户和状态目录，安装独立 Python 环境依赖，执行 witness 数据库迁移，然后安装本 service。443 绑定权限只授予该进程 `CAP_NET_BIND_SERVICE`。首次启动后从本机和公网映射分别验证证书链、主机名、健康状态、154 初始 epoch，以及 123 在有效租约期间被拒绝。轮换签名密钥须安排双公钥过渡并验证后再撤销旧密钥。私有 CA 的签发、信任分发、端口核验和回退步骤见 `neon-tavern-witness-private-ca.md`。

本说明与 unit 均为部署样例，不会自动创建用户、密钥、证书、反向代理或开放防火墙端口；生产安装前必须单独完成变更审查和备份。
