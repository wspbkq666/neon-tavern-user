# 154/123 容灾变更回滚清单

此清单只回退本次明确命名的 witness/霓虹酒馆文件和服务。回退不清空数据库、不删除快照、不切换仍在写入的主站，也不操作其他项目。

## 执行前核对

- [ ] 已确认当前 witness lease holder、epoch、到期时间和当前主站；若状态无法确认，停止任何切换/恢复操作。
- [ ] 已记录本次变更的配置备份位置和 SHA-256；配置备份仅含配置，不把私钥复制到普通备份目录。
- [ ] 已记录当前主站数据库与媒体快照 ID、摘要及最近一次成功复制时间。
- [ ] 目标操作仅涉及独立 `neon-tavern-witness` 或 `neon-tavern-web/worker` 服务；其他服务名不得出现在命令中。
- [ ] 原服务仍可访问，且 SSH/云控制台回退通道正常。

## 配置回退顺序

1. 若 witness TLS/租约状态异常，先停止计划中的新写主提升；**不撤销当前仍有效的主租约，不将两站强制改成可写**。
2. 如本次仅更新了 CA bundle 或 witness URL，恢复对应节点的配置备份；检查文件权限和环境变量没有泄露，再重新加载霓虹酒馆服务配置。
3. 改动过 Nginx 的情况下，恢复此前备份的霓虹酒馆专属站点配置，先执行 `nginx -t`；只有语法检查成功才 reload Nginx。**禁止 restart**，且不得操作未确认归属的共享配置。
4. witness unit 回退时恢复 witness 自身备份，先运行 `systemd-analyze verify <witness unit>`；成功后只 reload systemd manager 并重启明确命名的 witness service。若 `systemd-analyze` 失败，不重启服务并保留原状态。
5. 若业务应用 release 回退，必须使用部署前记录的霓虹酒馆专属 release 指针与数据库迁移兼容性结论；不得把旧数据库覆盖当前主站新数据。无法证明兼容时保持应用只读并等待人工核对。

```sh
systemd-analyze verify /etc/systemd/system/neon-tavern-witness.service
systemctl daemon-reload
systemctl restart neon-tavern-witness.service
```

上例最后两条只适用于 witness 专属 unit 且该单元此前已明确变更、备份和验证；共享站点配置只允许 `reload`，禁止 `restart`。任何不满足前提的场景均不得照抄命令。

## 验证与停止条件

回退后逐项执行服务健康检查并记录响应码、服务状态和证书校验结果。

- [ ] witness HTTPS 证书链、SAN、有效期和 CA bundle 验证通过。
- [ ] 当前主站健康路由、登录和只读状态 API 正常；状态 authority 与本地租约 holder/epoch 一致。
- [ ] 热备仍只读；数据库/媒体副本 ID 和摘要未被错误回滚。
- [ ] 原 Node/Agri Ledger、SillyTavern、NapCat 及其他站点健康状态未改变。
- [ ] 操作日志不含密码、私钥、Cookie、用户消息或模型密钥。

若 witness 不可达、lease holder 不明、数据库新旧不一致、语法检查失败、健康检查失败或回退会丢失当前主站数据：保持写入关闭/当前已确认主站继续服务，保留数据库和快照，停止自动化并报告证据。不得使用 `git reset`、删除快照、覆盖数据库或重启不相关服务。
