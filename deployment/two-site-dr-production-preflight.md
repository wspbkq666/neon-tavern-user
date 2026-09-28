# 154/123 与 Witness 容灾生产预检

本清单用于人工分阶段核对，不能把文档检查或本地测试当成线上验收。所有生产步骤默认只读；只有每一阶段单独通过门禁后，才执行该阶段明确批准的写操作。发现归属不明、输出含秘密、端口/证书/备份不确定时立即停止。

## 已知现状（来自此前只读盘点，执行前必须重新核验）

| 节点 | 此前观察 | 必须重新核验 |
|---|---|---|
| 154.222.26.47 | 80 由既有 Agri Ledger Vite 预览监听；8088 为霓虹酒馆用户应用；未观察到 443 listener；UFW 已允许 443；另有其他既有服务 | 80/443/8088 当前监听者、证书/代理、应用数据及服务健康 |
| 123.56.125.209 | Nginx 监听 80/443；霓虹酒馆在独立目录/服务；另有 NapCat Docker | Nginx 站点、证书有效期、霓虹酒馆版本/服务、Docker 容器及数据库/媒体备份 |
| Witness `hbe3.wch1.top` | Ubuntu 22.04；独立 `neon-witness` 用户/venv/unit 已部署；内网 `443` 由该 unit 监听；UFW inactive；根盘约 21G 可用、内存约 1.5GiB 可用 | 用户确认 `43111 -> 443`、`43511 -> 80`；公网高位 HTTPS 不稳定；154/123 已改用只绑定本机回环的 SSH 隧道访问 witness，并通过专用 CA 验证状态接口 |

端口事实：用户已确认 witness 公网 `43111 -> 内网 443`、公网 `43511 -> 内网 80`。Witness 仅使用 43111。154 的现有 `:80` Node 服务不得停止、重启、代理接管或改写行为；任何变更不得重启无关服务。既有 Agri Ledger、SillyTavern、NapCat 和其他站点属于保护范围。

## 最新外部只读探测（2026-09-25）

- `http://154.222.26.47:8088/api/health/`：HTTP 200。此入口仍为明文 HTTP，不代表浏览器安全传输。
- `https://123.56.125.209/api/health/`：HTTP 200，curl TLS 校验结果 `0`。
- `https://154.222.26.47/api/health/`：无法连接 443；当前不能把 154 作为可信 HTTPS 入口。
- `http://154.222.26.47/`：HTTP 200，页面标题为“农批记账”；Agri Ledger 仍由原有 80 端口服务提供。
- 映射由用户通过服务商控制台确认：`43111 -> 443`、`43511 -> 80`。witness 本机使用根 CA 与 SAN 校验 `https://hbe3.wch1.top/api/lease/status/` 返回 HTTP 200、TLS 校验 0、租约为空。
- 外部复核：`43111` TCP 可达；抓包看到外部 SYN 到达 witness 内网 `443` 并完成 TCP 握手，但外部客户端未完成 TLS 握手，Node/PowerShell 请求均复位。`43511` TCP 探测失败。当前不能把公网 witness TLS 判为通过或启用租约。
- 绕过公网高位 TLS 映射后，154 与 123 分别通过公网 SSH `43653` 建立到 witness 的持久隧道。两台业务机的 `neon-tavern-witness-tunnel.service` 均为 active，监听仅为 `127.0.0.1:43111`；本机将 `hbe3.wch1.top` 解析到 `127.0.0.1`，因此应用保留原证书主机名与 CA 校验。两台机器各自的租约状态请求均返回 HTTP 200、租约为空。
- 隧道使用每站独立 Ed25519 密钥；witness `authorized_keys` 仅允许转发到 `127.0.0.1:443`，并禁止远程命令。服务器侧密钥、CA 公共证书、主机密钥分别存于 `/etc/neon-tavern-dr/`；修改前备份分别为 witness `/root/.ssh/authorized_keys.backup-20260925` 与 `...backup-20260925-123`，两业务机 `/etc/hosts.neon-tavern-backup-20260925`。业务应用 `.env` 尚未配置，容灾与数据同步仍关闭。
- 在 Agri Ledger 既有 `frontend/dist` 临时放置一个唯一 ACME 探测文件后，公网请求被 Vite SPA 回退为农批记账首页而非文件内容。已删除该精确临时文件及本次创建的空目录，未改源代码、构建产物或重启 Agri Ledger。
- 154 现有 Certbot 5.4.0 的 standalone 与 webroot 插件均只支持 HTTP-01；当前验证目录被 SPA 回退，不能用现状签发 IP 证书。未启动 443 验证服务、未安装证书、未修改任何 154 服务或环境配置。
- witness 已使用用户提供的 SSH 凭据完成交互式登录，仅执行 `hostnamectl`、`ss`、systemd 服务清单、`df`、`free` 和目录盘点；没有创建文件、安装软件、开放防火墙或启动服务。现场确认 Ubuntu 22.04.1、内网 `:443` 空闲、没有已运行应用服务、`/opt` 与 `/srv` 无项目目录、UFW inactive。凭据未写入文档或命令参数。
- 本轮经 123 SSH 跳转完成 154 只读复核：80 为 `/root/agri-ledger/frontend` 的 Vite 预览，443 无 listener，8088 为 `/opt/neon-tavern-user` 的 Gunicorn；UFW 已有 443 入站规则。应用 `:8088/api/health/` 返回 HTTP 200。新建的霓虹酒馆 HTTPS 服务若继续部署，必须作为独立服务运行，不改变现有 `:80` Node 服务。
- 映射确认后仅在独立 witness 主机部署 `/srv/neon-tavern-witness`、`neon-witness` systemd unit、专用 venv、SQLite 状态库和私有 CA 叶证书；未改 UFW。systemd 为 active，Gunicorn 监听 `0.0.0.0:443`；本机 CA/SAN HTTPS 状态检查返回 200。根 CA 私钥留在开发机受 ACL 与 DPAPI 保护的用户目录，未上传。
- 根证书 SHA-256：`db:b1:16:b5:2b:af:93:3d:22:da:2b:f8:7f:f7:cb:6e:10:dc:7a:2c:64:99:7b:4e:29:2c:d9:bb:40:3b:86:03`；witness 叶证书 SAN 为 `DNS:hbe3.wch1.top`，有效期至 2026-12-23。
- witness 本机 `api/lease/status/` 通过专用 CA 返回 HTTP 200、`TLS=0`、租约为空。开发机公网 `43111` 多次 TLS 请求复位；抓包证明 TCP 映射到达内网 listener，但尚未观察到一次完整的公网 TLS 请求。公网连通性未达到可启用租约的稳定标准。
- 123 SSH 只读确认现有 `neon-tavern-web.service` 与 worker 正常，Nginx 配置检查通过；`/etc/neon-tavern.env` 中灾备开关/节点 ID/节点私钥均未配置。未改 123 服务或环境。

阶段 B 已部署 witness；公网高位 TLS 映射未通过，但改由 154/123 的 SSH 隧道实现端到端加密，且两节点均通过 witness CA 状态检查。阶段 C 的浏览器入口仍受阻：154 没有可信 HTTPS，Agri Ledger Vite 不提供 ACME challenge 文件；不得为了证书改写或重启原服务。业务应用尚未接入隧道，123/154 节点签名密钥、初始复制和切换仍保持关闭。

## 阶段 A：只读盘点

逐台记录 UTC 时间、执行主机、只读命令、退出码和必要摘要。不要输出 `.env`、systemd Environment、私钥、数据库行、用户消息或完整代理配置。

Linux 只读检查示例：

```sh
hostnamectl --static
ss -lntp
systemctl --no-pager --type=service --state=running
systemctl show <明确的服务名> -p ActiveState,SubState,MainPID,WorkingDirectory
df -h
free -h
```

仅在确定目标是 Nginx 后可运行 `nginx -t` 做语法检查；`nginx -T` 可能打印密钥/认证信息，未经脱敏审查不得收集或分享。TLS 只读取证书公开信息，例如 `openssl x509 -in <公开证书路径> -noout -dates -subject -ext subjectAltName`。数据库只允许记录版本、文件大小和受控摘要，不查询真实用户内容。

阶段 A 记录：

- 154 端口/服务/路径归属：________________；80 Node 仍健康：□是 □否。
- 123 Nginx/站点/证书/服务：________________；NapCat 未改变：□确认。
- Witness SSH/端口/磁盘/NAT 控制台映射截图或记录：________________。
- 备份文件及数据库/媒体摘要已读回核验：□是 □否；位置：________________。
- **停止条件：** 任何路径或进程归属不明、备份不可读、共享配置无备份、SSH 控制通道不可靠。

## 阶段 B：Witness 隔离安装（仅映射确认后）

前置：控制台明确确认公网 `43111 -> witness 内网 443`，或由用户明确提供实际映射；根 CA 与叶证书已按 `neon-tavern-witness-private-ca.md` 安全准备；Witness 本机没有占用目标内网端口的项目；不依赖开放公网 80。

1. 备份 witness 专属 unit、环境文件、数据库和证书路径权限记录。
2. 创建独立 `neon-witness` 账户、venv 和 `/var/lib/neon-tavern-witness`；不改动其他账户、路径或服务。
3. 安装证书及环境变量，检查证书 SAN/有效期/私钥权限；先在本机以 CA 验证 HTTPS。
4. 验证租约状态接口、154 初始租约及 123 被拒绝；从 154/123 使用同一 CA bundle 验证 HTTPS 和主机名。
5. 从外网验证已确认的公网高位端口转发到内网 443。

**停止条件：** 映射不通、SAN/信任链错误、私钥权限不当、SSH 不稳定、目标端口已有其他服务或必须关闭 TLS 验证才能连接。失败只回退 witness 自己的 unit/文件；不修改 UFW，除非已有独立控制台、确认 SSH 真实端口且变更获准。

阶段 B 结果：□未开始 ☑节点到 witness 的加密通道已验证 □完整容灾通过；证据：154/123 隧道服务 active、只绑定 loopback，CA 校验状态接口均 HTTP 200；公网高位 TLS 映射仍不稳定。

## 阶段 C：业务站点 HTTPS 与应用配置

默认仍建议浏览器访问使用可信 HTTPS。用户已明确选择个人 HTTP 模式：154 保留现有霓虹酒馆 `8088` HTTP 入口，不改 80 上的 Node/Agri Ledger，也不要求新增公网证书。123 的现有 HTTPS 证书仍须验证有效。

该选择只放宽本人访问 154 时的浏览器传输，不代表 HTTP 登录安全。154 浏览器可以探测 HTTPS 的 123 并在已确认接管后跳转；位于 HTTPS 123 的页面无法自动探测 HTTP 154，因此 154 恢复并完成受控回切后，用户要手动重开 154 地址。业务数据复制优先走 154 到 123 的 HTTPS；反向回传仅在 123 显式设置 `TAVERN_DR_ALLOW_HTTP_PEER=1` 并精确列出 `154.222.26.47` 后，发送 AES-GCM 加密且 Ed25519 签名的快照。此开关不关闭 123 的浏览器 HTTPS，也不得用于其他站点。

154 端口 80 的现有 Node 服务不得停止、重启、代理接管或改写行为。站点级 HTTPS 如日后需要，须另行评估独立证书和监听方案；当前个人模式下不因证书要求阻塞部署，但不得称浏览器到 154 的访问已加密。

如已有明确独立入口方案且获准：每个目标 `.env` 和服务 drop-in 修改前备份；仅加入 `TAVERN_WITNESS_URL`、`TAVERN_WITNESS_CA_BUNDLE`、精确 `TAVERN_DR_STATUS_ALLOWED_ORIGINS` 与节点配置；先离线检查 Django settings/环境语法，再在 staging 健康检查。共享代理配置必须备份、语法检查后仅 reload。不得在运行中的 Node 主服务上操作。

阶段 C 结果：□未开始 □通过 □因 154 HTTPS 停止；证据/原因：________________。

## 阶段 D：复制与切换

仅在阶段 B/C 通过后继续。确认新隐私告知版本已经向所有相关用户展示并同意；未同意用户数为零。确认两站代码版本、迁移、节点签名密钥、公钥、快照加密密钥版本一致；为两站数据库和媒体做可恢复备份并记录摘要。首次传输使用批准的数据范围，禁止传输服务器凭据、模型 API 密钥或无关项目。

先复制到隔离暂存目录，校验 SHA-256 清单、SQLite `PRAGMA integrity_check`、媒体数量/摘要、迁移和版本；确认热备保持只读且只操作命名明确的霓虹酒馆服务。完成登录、聊天读写、状态 API、角色/世界书和副本健康检查。真实提升/回切必须是独立受控演练，获得新 epoch 后验证另一节点写保护，再决定是否启用自动切换。

阶段 D 结果：□未开始 □通过 □停止；摘要/测试/复核人：________________。

## 保护范围与完成定义

- 任何步骤不得对 Agri Ledger、SillyTavern、NapCat、154 的既有 Node 服务或其他租户站点执行 stop/restart/改配置。
- 配置改动前备份；语法检查通过后仅 reload；reload 后检查原站点和霓虹酒馆健康路由。
- “服务启动”不等于容灾上线。需记录两站 HTTPS、witness HTTPS、最后成功副本时间/RPO、租约状态、登录和恢复回切测试证据。
- 154/123 到 witness 的 SSH 隧道与 CA 验证已通过，但应用 `.env`、站点密钥、快照复制、用户同意与真实切换演练尚未完成；154 仍无浏览器可信 HTTPS。以上未完成前不可标记生产容灾已启用。
