# 用户版一键安装

## 安装前准备

- 一台 Ubuntu 22.04/24.04 LTS、x86_64、使用 systemd 的 Linux 服务器，并可用 root/sudo。
- 已安装 Git；安装器不会在预检和确认之前修改系统。
- 一个已解析到该服务器公网地址的域名，公网 TCP 80/443 可达。安装器会使用 Let’s Encrypt 自动申请 HTTPS 证书。
- 从总站 154 和总站 123 分别取得一个有效的一次性分站配对码。两个配对码不能互换；配对码会在终端隐藏输入。
- GitHub 用户版仓库地址和完整的 40 位 release commit SHA。不能使用管理员版/总站仓库。

用户版 release 必须含 `deployment/user-edition-release.json`，声明用户版、已排除全局管理员代码，并将两总站监管设为必需。安装器会扫描后端和前端生产代码并核对双总站地址；不符合边界的仓库不会安装。项目入口位于仓库根目录，安装后代码、虚拟环境和数据分别隔离。

## 一条命令启动

当前用户版发行提交：`8daac40a728127e333dd67cf488c7cfb36dba0ac`。以下命令固定安装该发行版本：

```bash
curl -fsSL "https://raw.githubusercontent.com/wspbkq666/neon-tavern-user/8daac40a728127e333dd67cf488c7cfb36dba0ac/deployment/bootstrap.sh" \
  | sudo env NEON_TAVERN_USER_REPO_URL="https://github.com/wspbkq666/neon-tavern-user.git" \
      NEON_TAVERN_RELEASE_SHA="8daac40a728127e333dd67cf488c7cfb36dba0ac" bash
```

bootstrap 通过 Git 检出固定 commit，不下载压缩包或远程打包文件。随后安装器会先只读检查系统、DNS、监听端口、Web 服务、Nginx 域名、systemd 单元和目标目录，再显示变更摘要。只有输入“安装”后才会安装 apt 依赖、写入本站文件或操作本站服务。

安装器会一并安装 `antiword`，用于服务器端读取 DOC 文件；DOCX 读取依赖随 Python requirements 安装。智能导入需要用户登录后在模型设置中配置有效的模型服务和 API 密钥。

## 安装时会询问

1. 本站公网域名、站点显示名称、证书续期邮箱。
2. 154 与 123 分别签发的一次性配对码。输入时不会回显。
3. 最后显示域名、版本、服务名、数据目录和备份目录；输入“安装”确认。

安装器固定分站角色为 `satellite`，总站地址固定为 `https://154.222.26.47` 和 `https://123.56.125.209`。用户版没有关闭监管或改绑地址的安装选项。任一端未登记时，Web 服务的启动前检查会失败，登录/注册不会开放。

## 安装位置与服务

- 代码 release：`/opt/neon-tavern-user/releases/<commit-sha>/`
- 当前版本链接：`/opt/neon-tavern-user/current`
- 环境与系统凭据：`/etc/neon-tavern-user/`
- 用户数据库和媒体：`/var/lib/neon-tavern-user/`
- 升级备份：`/var/backups/neon-tavern-user/`
- systemd：`neon-tavern-user-web`、`neon-tavern-user-worker`，以及双总站同步和命令轮询 timer
- Nginx：独立 `neon-tavern-user-<域名>` 配置；不会改写 default 或其他站点配置

完成后访问 `https://你的域名/login/`。新部署的第一个注册账号成为本站网站管理员；`/admin/` 是本站管理员入口。

## 升级

使用同一条命令并传入新的固定 SHA。安装器确认配置目录和服务文件由自身管理后，会先备份环境与 SQLite 数据库，再只停止本站 web/worker，迁移完成后切换 release。备份失败或迁移失败时不会切换当前版本；若迁移后健康检查失败，会停止本站服务、保留新 release 和数据库备份供诊断，不会冒险用旧代码打开新数据库。其他站点的服务不会被停止或重启。

## 常见停止原因

- 域名没有 IPv4 A 记录：先设置 DNS 并等待解析生效。
- 80/443 被 Apache、Caddy 或其他进程占用：先确认宿主机架构；安装器不会替你停服务或改端口。
- 域名已出现在其他 Nginx server block：选一个未使用的域名，不要手工覆盖现有配置。
- Nginx 已安装但处于停止状态：先核对宿主机现有站点，再由站点所有者决定是否启动 Nginx。
- 任一总站配对失败：查看本站服务日志，向失败的总站重新签发该端的一次性配对码，再以同一 SHA 安全重试。
- 登录页健康检查失败：查看 `journalctl -u neon-tavern-user-web -u neon-tavern-user-worker` 与 Nginx error log；保留备份，不要删除数据目录。

安装器不提供破坏式卸载。不要手工删除 `/var/lib/neon-tavern-user/` 或备份目录。

