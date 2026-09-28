# 霓虹酒馆用户版

适用于个人或独立部署，包含账号注册与登录、对话、角色卡、世界书、模型设置、素材市场、整合包导入导出和本站管理员面板 `/admin/`。

本版本不包含全局管理员能力：跨站账号/服务器管理、全局更新发布、跨站管理员授权以及总站管理面板。分站代码固定登记至总站 154 和 123，不能关闭或自行改绑监管。

## 本地运行

1. 安装 `requirements.txt` 中的 Python 依赖。
2. 按 `.env.example` 配置变量；生产环境须设置随机 `TAVERN_SECRET_KEY`、`TAVERN_ENCRYPTION_KEY` 和正确的 `TAVERN_ALLOWED_HOSTS`。启动服务负责加载 `.env`，Django 不会自动读取它。
3. 执行 `python manage.py migrate`，启动 Django/Gunicorn 后从 `/login/` 注册。本站首个账号成为本站管理员。

## AI 智能导入

登录后打开“素材”页面中的“AI 智能导入”，粘贴设定文字，或选择 TXT、MD、DOCX、DOC 文件。账号需要先在“我的 → 模型设置”配置可用的模型服务和 API 密钥；智能导入在服务端读取现有设置，不会把密钥发送给浏览器。

一次只使用一种输入。上传文件最大 5 MiB，提取文字最大 100,000 个字符；较长内容会按段落分块分析。AI 会把 NPC 与玩家卡分别整理为对应角色卡，并识别世界书条目及角色范围。识别结果先作为草稿显示，用户可更改类别和字段、跳过单条内容，再检查整合包预览；只有点击“确认并一键导入”后才写入账号。

DOCX 由 `python-docx` 读取；DOC 由服务器端的 `antiword` 提取。项目安装器会安装该系统依赖，本地部署需自行安装 `antiword`。首版不读取扫描图片中的文字。角色卡开场白、备用开场白和内嵌角色专属世界书没有本地对应字段，预览会提示并保留原文片段供核对，不会静默写入其他字段。

不要将 `.env`、数据库、模型 API 密钥、节点私钥或 Witness 密钥提交到 Git。

## 多站点部署

每个霓虹酒馆部署都是独立站点，各自保存账号、角色卡、世界书、对话和设置，同名账号也互相独立。受监管分站会将账号目录和用户在确认新版协议后新增或修改的完整私聊记录，分别同步到两个总站用于管理与后台审查；任一总站暂时不可达时，另一个仍独立收件，失败队列保留在本站重试。同步通过 HTTPS 和站点 Ed25519 签名保护。隐私协议与用户使用规则会在注册页面要求确认，未确认的账号目录和私聊不会上报。异地容灾是另一套独立能力，不等同于监管数据镜像；不得把两个独立活动站配置成相互覆盖的主备。

## 一键安装

Ubuntu 22.04/24.04 LTS 可使用 [`deployment/README-user-install.md`](deployment/README-user-install.md) 中的一条命令安装。需要公网域名、HTTPS 所需的 80/443 端口，以及总站 154 和 123 分别签发的一次性分站配对码。安装器只管理 `neon-tavern-user-*` 服务、独立数据目录和本站 Nginx 配置；任一总站登记失败时不会开放登录。

启用异地容灾前，阅读 [`deployment/two-site-dr-user-rollout.md`](deployment/two-site-dr-user-rollout.md)、[`deployment/two-site-dr-production-preflight.md`](deployment/two-site-dr-production-preflight.md) 和 [`deployment/two-site-dr-rollback-checklist.md`](deployment/two-site-dr-rollback-checklist.md)。容灾默认关闭；先完成两站备份、同意确认、初始快照和租约验证，再开启 `TAVERN_DR_ENABLED`。

个人 HTTP 模式下，154 的浏览器登录与对话流量未经过 TLS 加密，仅适合本人使用。154 页面可在 Witness 确认 123 接管后跳转；从 HTTPS 的 123 自动回到 HTTP 的 154 受浏览器混合内容限制，回切后需手动重新打开 154 地址。
