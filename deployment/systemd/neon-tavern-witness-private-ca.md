# Witness 私有 CA 与 HTTPS 部署

本流程用于独立的挂机宝 witness。它不要求公网 DNS 控制权，也不使用公开证书；154/123 的服务端 Python 客户端通过单独 CA bundle 验证 witness。浏览器不直接连接 witness。

## 密钥和证书边界

- 根 CA 私钥只在受控离线电脑生成并保存；不上传 witness、154、123，不放在项目目录、云盘同步目录、Git 仓库或普通服务器备份中。
- 根 CA 公共证书可安全分发至 154、123；文件内容不是秘密，但应经可信渠道核对 SHA-256 指纹。
- witness 只部署叶证书及对应私钥。证书必须包含 `DNS:hbe3.wch1.top` SAN、`serverAuth` EKU，并在配置有效期内。
- 叶私钥目录由 root 持有，文件权限限制为服务所需的最小读取范围；Django/应用日志不得输出私钥或环境文件。
- 禁止关闭 TLS 校验、使用 `verify=False`、忽略主机名错误，或在证书错误时回退到 HTTP。

## 离线签发步骤

在受控离线环境中创建加密保护的根私钥和根证书。将根私钥备份到离线介质并验证可读后，断开/封存该环境；后续只在需要签发/轮换时短暂使用。

生成 witness 叶密钥和 CSR。签名扩展文件至少包含：

```text
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:hbe3.wch1.top
```

用离线根 CA 签发有限有效期的叶证书，将叶证书链和叶私钥通过受控渠道送到 witness。将根 CA **公共证书**分别传到 154 和 123。部署前查看证书内容并确认 SAN、有效期及 SHA-256 指纹：

```sh
openssl x509 -in witness-fullchain.pem -noout -subject -issuer -dates -ext subjectAltName -fingerprint -sha256
```

## witness 服务配置

1. 先确认服务器商控制台显示的公网到内网映射，并从外网验证目标端口能到达 witness 内网 443。当前已由用户确认公网 `43111 -> 443`、`43511 -> 80`；本方案只使用 `43111`，不使用公网 80。
2. 在 witness 设置 `WITNESS_TLS_CERT`、`WITNESS_TLS_KEY` 文件路径并安装 `neon-tavern-witness.service.example`。该 service 由专用用户运行，仅附加绑定低端口所需的 `CAP_NET_BIND_SERVICE`。
3. 启动前验证 unit 配置和文件权限；启动后在 witness 本机使用 `curl --cacert <根CA公共证书> --resolve hbe3.wch1.top:443:127.0.0.1 https://hbe3.wch1.top:443/api/lease/status/` 验证链与主机名。
4. 从 154、123 分别使用相同 CA bundle 和 URL `https://hbe3.wch1.top:<已核实的公网映射端口>` 验证状态查询。必须确认未经信任的 CA 和错误 SAN 均连接失败。
5. 不启用 UFW，除非已先确认并放行实际 SSH 端口及 witness 服务流量，且具备独立控制台回退方式。不得修改 154/123 防火墙来解决 witness 映射问题。

## 业务节点配置与验证

将根 CA 公共证书分别放在仅 root 可修改的位置，并在霓虹酒馆服务环境中设置：

```ini
TAVERN_WITNESS_URL=https://hbe3.wch1.top:<已核实的公网映射端口>
TAVERN_WITNESS_CA_BUNDLE=/etc/neon-tavern/witness-ca.pem
```

此 CA bundle 仅用于 witness HTTP 客户端，不改变系统证书库或其他 HTTP 客户端。通过真实客户端执行状态查询、租约签发和续期测试；检查证书过期、CA 错误和 SAN 错误都会导致 witness 不可用并让节点失败关闭。

## 轮换、吊销和回退

- 叶证书到期前先离线签发新证书；在 witness 保持可用的变更窗口中替换叶证书，语法/证书检查通过后仅重载明确命名的 witness 服务，验证 154/123 TLS 查询后再清理旧叶密钥。
- 根 CA 轮换需先把新根公共证书安全分发到两站，并在专用客户端配置支持的过渡信任 bundle；验证新叶证书成功后才撤销旧根。私钥泄露时立即停止租约签发、隔离 witness、生成新 CA 并重新配对。
- 配置前备份 witness 自己的 unit、环境文件和证书；失败时只恢复 witness 备份并 reload witness，不触碰 154、123 或其他应用服务。
- 映射不通、SAN/链错误、证书过期或 SSH 管理通道不可靠时停止部署；不得临时改 HTTP、关闭校验或开放额外端口绕过错误。
