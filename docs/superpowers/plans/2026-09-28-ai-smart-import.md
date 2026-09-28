# AI 智能导入实施计划

> **执行状态：** 实现已完成，109 项后端测试、49 项前端测试和用户版安装脚本测试套件通过；当前改动保存在 `feat/ai-smart-import` 分支，未合并或部署。

> **执行说明：** 实施人员必须按任务顺序执行本计划；每个任务先补失败用例，再实现、验证并提交。

**目标：** 在霓虹酒馆用户版新增独立的 AI 智能导入页面，能从粘贴文本或 TXT、MD、DOCX、DOC 中识别世界书、NPC 角色卡和玩家卡，并经预览后复用现有整合包导入链路。

**架构：** 新增受登录保护的文本提取和 AI 解析接口；统一草稿转换成 `neon-tavern-bundle` v1。前端提供独立页面、字段编辑和分类确认，再调用现有整合包预览与提交接口，因此素材仍由当前权限、校验和数据库事务逻辑管理。

**技术栈：** Django 5.2、Python、`python-docx`、Ubuntu `antiword`、现有 `httpx`/模型调用封装、静态 HTML/JavaScript/CSS、Django TestCase、Node 内置测试运行器。

**设计规格：** [../specs/2026-09-28-ai-smart-import-design.md](../specs/2026-09-28-ai-smart-import-design.md)

## 全局约束

- 只在用户版仓库实施；不得触碰全局管理员、总站登记、密钥或其他站点的能力和配置。
- API 仅服务已登录用户；AI 密钥只在服务端通过 `active_api_key(user)` 读取，不能返回浏览器或写入日志。
- 输入文件最大 5 MiB；提取文本最大 100,000 字符，并按标题/段落最多每块 12,000 字符、重叠 300 字符；生成结果最多 100 张角色卡、100 本世界书，并保持现有整合包 20 MiB 上限。
- 导入草稿必须通过现有 `/api/bundles/import/preview/` 校验；写入只能经 `/api/bundles/import/commit/` 完成。
- NPC 映射为 `Character.is_player_controlled=false`；玩家卡映射为 `true`，不得自动改写 `UserProfile.inferred_traits`。
- 对无法映射到现有 `Character` 字段的开场白、备用开场白及角色专属世界书，在预览中明确警告，不得静默丢弃。
- 所有页面文案、错误提示和新增文档使用中文；不执行上传文件中的宏、脚本或链接。

## 重点复核输入

- 损坏或伪装扩展名的 DOC/DOCX：拒绝进入 AI 调用，显示可操作错误。
- 混有世界书、NPC、玩家设定的长文：分段保留来源，逐段分类，不把一类覆盖另一类。
- 含有指令性文本或试图改变解析规则的文件：只按固定 JSON 结构抽取，文档指令不改变系统解析要求。
- 模型返回无效 JSON、超出数量或字段结构不符：拒绝提交并展示修复/重试提示。
- 重名角色、世界书以及角色范围关联：保持整合包现有“保留副本”规则和包内引用映射，不覆盖用户资料。

---

### Task 1：实现受限的 TXT、MD、DOCX、DOC 文本提取

**文件：**
- 新建：`core/smart_import_extract.py`
- 新建：`core/test_smart_import_extract.py`
- 修改：`requirements.txt`
- 修改：`deployment/install.sh`
- 修改：`deployment/tests/install-config.bash`

**接口：**
- 提供 `ExtractedDocument(filename: str, text: str, warnings: list[str])` 数据类。
- 提供 `extract_document(*, text: str | None = None, upload=None) -> ExtractedDocument`；文字与文件只允许二选一。
- 对文件校验扩展名、5 MiB 上限和空内容；对提取后文本实施 100,000 字符上限。

- [ ] **步骤 1：写提取器失败用例**

在 `core/test_smart_import_extract.py` 用 Django `SimpleTestCase` 覆盖 TXT UTF-8 BOM、MD 标题保留、DOCX 段落与表格文字、未知扩展名、超限文件、损坏 DOCX、空文件，以及错误扩展名不得调用 DOC 解析器。

- [ ] **步骤 2：运行用例确认失败**

运行：`python manage.py test core.test_smart_import_extract -v 2`

预期：因提取模块和函数尚不存在而失败。

- [ ] **步骤 3：实现提取器及依赖**

DOCX 使用 `python-docx` 读取段落和表格；在 `requirements.txt` 固定兼容 Django 5.2 的 `python-docx` 版本。DOC 只通过 `antiword` 子进程提取，参数使用独立临时文件绝对路径、关闭 shell、限制 15 秒、检查返回码并清理临时目录；缺少可执行文件时返回中文安装提示。把 `antiword` 加到 `deployment/install.sh` 的 apt 安装清单，并在安装脚本测试里确认依赖名单包含它。

用例调用示例：

```python
upload = SimpleUploadedFile("设定.txt", b"# NPC\n林雾")
result = extract_document(upload=upload)
assert result.filename == "设定.txt"
assert result.text == "# NPC\n林雾"
assert result.warnings == []
```

- [ ] **步骤 4：运行提取器与安装脚本用例**

运行：`python manage.py test core.test_smart_import_extract -v 2` 和 `bash deployment/tests/install-config.bash`。

预期：所有提取边界用例通过；安装配置测试确认 Ubuntu 安装包含 antiword。

- [ ] **步骤 5：提交任务**

提交信息：`feat: add bounded smart import document extraction`。

### Task 2：实现 AI 分类、草稿校验和整合包映射

**文件：**
- 新建：`core/smart_import.py`
- 新建：`core/test_smart_import.py`
- 参考：`core/generation.py`
- 参考：`core/character_api.py`
- 参考：`core/worldbook_transfer.py`
- 参考：`core/bundle_transfer.py`

**接口：**
- 提供 `analyze_document(text: str, *, options: dict, api_key: str) -> dict`，通过现有 `call_json_model()` 解析结构化结果。
- 提供 `normalize_smart_import(result: dict) -> tuple[list[dict], dict]`，返回前端草稿及 `neon-tavern-bundle` v1 负载。
- 模型结果顶层固定为 顶层必须有 `items` 数组，每项包含 `type`、`fields`、`source_excerpt`、`confidence`、`warnings`；类型限于 `worldbook`、`npc`、`player`、`unknown`，字段与当前模型对齐。

- [ ] **步骤 1：写分类和映射失败用例**

覆盖 NPC/玩家布尔映射、世界书条目及关键词、混合多类条目、分块与重叠片段去重、空名称、未知类别、非字典字段、100 项上限、模型返回额外字段、开场白/备用开场白警告、角色专属世界书警告、恶意指令文本不得进入系统消息规则。

- [ ] **步骤 2：运行用例确认失败**

运行：`python manage.py test core.test_smart_import -v 2`

预期：因解析器尚不存在而失败。

- [ ] **步骤 3：实现 JSON 提示词和严格归一化**

系统消息只定义分类枚举和允许字段，文档正文作为独立用户数据传入。按 Markdown/Word 标题和段落分块，每块上限 12,000 字符，边界保留 300 字符重叠；由服务层根据块序号去重重叠内容并把重名片段标为待确认。拒绝无效类别、必需字段缺失和超量项目；只从白名单字段构建输出。对无专用模型字段的内容保留原文摘要和警告供预览，不塞入语义不匹配字段。NPC 与玩家统一输出 `characters` 行，并以 `is_player_controlled` 区分。世界书行输出 `core.worldbook_transfer.parse_import(worldbook_payload, "native")` 可验证的 `format/version/worldbook/categories/entries` 结构。生成 `package_id`，并将同文档中世界书角色范围名称映射到包内角色。

角色字段映射固定为：`name→name`、`description→summary`、`personality→personality`、`scenario→memories`、`mes_example→speech_habits`、`relationship_notes→relationship_notes`、`state_fields→state_fields`、`categories→categories`。只映射项目实际接收的字段；其余值进入待确认警告。

- [ ] **步骤 4：运行解析器用例确认通过**

运行：`python manage.py test core.test_smart_import -v 2`。

预期：所有分类、白名单、数量和信息保留警告用例通过，AI 未调用时不需要外部网络。

- [ ] **步骤 5：提交任务**

提交信息：`feat: normalize smart import drafts into bundle v1`。

### Task 3：新增登录保护的 AI 预览 API

**文件：**
- 新建：`core/smart_import_api.py`
- 新建：`core/test_smart_import_api.py`
- 修改：`tavern/urls.py`
- 参考：`core/auth_api.py`
- 参考：`core/character_api.py`
- 参考：`core/settings_api.py`
- 参考：`core/bundle_api.py`

**接口：**
- 新增 `POST /api/smart-import/preview/`。
- JSON 请求格式：`{"text": "她叫林雾，是一名在雾城巡夜的医生。"}`；文件请求用 multipart `file` 字段。
- 成功返回 JSON 对象，字段为 `drafts` 数组、`payload` 对象、`bundle_preview` 对象和 `warnings` 数组。

- [ ] **步骤 1：写 API 失败用例**

使用 Django `TestCase` 覆盖匿名 401、没有 API 密钥时返回可操作错误、文本与 multipart 输入、调用 `effective_values()` 与 `active_api_key()`、模型调用成功、解析失败返回 400、AI 超时返回中文可重试错误、输入限制、预览调用 `parse_bundle()` 且不写数据库。

- [ ] **步骤 2：运行 API 用例确认失败**

运行：`python manage.py test core.test_smart_import_api -v 2`

预期：因路由和处理器尚不存在而失败。

- [ ] **步骤 3：实现 API 和 URL**

API 复用 `authentication_error()`、`body_or_error()`、`effective_values(request.user)`、`active_api_key(request.user)`、`call_json_model()` 与 `parse_bundle(payload, request.user)`。API 密钥为空时不调用模型。捕获 DOC 解析异常、模型连接/JSON 异常和整合包校验异常，分别映射到安全的中文错误消息；不回传异常详情、密钥或完整文档。只有 POST 可访问并保持 Django CSRF 中间件保护。

```python
path("api/smart-import/preview/", smart_import_api.preview),
```

- [ ] **步骤 4：运行 API 用例确认通过**

运行：`python manage.py test core.test_smart_import_api -v 2`。

预期：接口返回经过整合包解析器验证的负载，预览不创建 `Character` 或 `Worldbook` 记录。

- [ ] **步骤 5：提交任务**

提交信息：`feat: add authenticated smart import preview api`。

### Task 4：实现独立智能导入页面与交互

**文件：**
- 修改：`frontend_dist/index.html`
- 修改：`frontend_dist/assets/prototype-app.js`
- 新建：`frontend_tests/smart-import-ui.test.mjs`
- 参考：`frontend_tests/material-panel-navigation.test.mjs`
- 参考：`frontend_tests/market-bundle-publishing.test.mjs`

**接口：**
- 页面面板 ID：`smartImport`；由导航按钮直接打开。
- API 顺序：`POST /api/smart-import/preview/` → 用户编辑草稿 → `POST /api/bundles/import/preview/` → 用户确认 → `POST /api/bundles/import/commit/`。

- [ ] **步骤 1：写浏览器行为失败用例**

Node 测试从 `frontend_dist/index.html` 和 `prototype-app.js` 验证入口存在、`openPanel("smartImport")` 可达、支持文本和四种扩展名、预览前不会提交、分类可改、草稿可跳过、确认时提交编辑后的负载、失败时保留输入并显示中文错误。

- [ ] **步骤 2：运行前端用例确认失败**

运行：`node --test frontend_tests/smart-import-ui.test.mjs`。

预期：因页面和行为尚不存在而失败。

- [ ] **步骤 3：实现面板和状态流程**

在 `index.html` 新增独立导入面板及样式，并递增页面 JS 资源版本号以清理旧缓存；增加文本框、文件选择、处理状态、分类草稿列表、每条保留/跳过控制、字段编辑区、警告区和确认按钮。在 `prototype-app.js` 中加入独立状态对象与事件；处理模型预览、草稿编辑、重新生成当前整合包负载、调用现有 bundle 预览、确认提交、错误状态与成功回到角色/素材面板。编辑后的角色数量和世界书数量需在最终确认页显示。

分类变更规则：`npc` 设置 `is_player_controlled=false`，`player` 设置 `true`，`worldbook` 移入 `worldbooks`；跳过项从提交负载移除。只在用户按最终确认后提交；请求按钮禁用以防重复提交。

- [ ] **步骤 4：运行前端用例确认通过**

运行：`node --test frontend_tests/smart-import-ui.test.mjs`。

预期：覆盖入口、编辑、跳过、警告、预览后确认和错误恢复。

- [ ] **步骤 5：提交任务**

提交信息：`feat: add standalone smart import workflow`。

### Task 5：端到端回归、安装说明和使用说明

**文件：**
- 修改：`README.md`
- 修改：`deployment/README-user-install.md`
- 新建：`core/test_smart_import_integration.py`
- 视安装测试结果修改：`deployment/tests/install-config.bash`

**接口：**
- 端到端测试通过 Django API client 验证“输入→模拟模型→草稿→现有 bundle 预览→提交→数据库记录”完整链路。

- [ ] **步骤 1：写完整链路回归用例**

用 `unittest.mock.patch` 替换外部模型调用，输入混合世界书、NPC 与玩家设定；验证 AI 负载经预览后提交生成 1 本世界书和 2 张角色卡，玩家/NPC 标记正确，包内世界书关联映射到新角色 ID，开场白无法映射时告警仍在响应中。

- [ ] **步骤 2：运行回归用例确认失败**

运行：`python manage.py test core.test_smart_import_integration -v 2`。

预期：当前尚无智能导入路由时失败。

- [ ] **步骤 3：补齐文档和安装说明**

在 README 增加功能入口、模型密钥前置条件、支持格式、5 MiB/100,000 字符限制、预览与确认流程、DOC 依赖和开场白字段提示。确保一键安装将 antiword 安装到本站服务器，Python 依赖随 requirements 安装。

- [ ] **步骤 4：运行定向回归**

运行：`python manage.py test core.test_smart_import_extract core.test_smart_import core.test_smart_import_api core.test_smart_import_integration -v 2`、`node --test frontend_tests/smart-import-ui.test.mjs`、`bash deployment/tests/install-config.bash`。

预期：所有新增回归通过。

- [ ] **步骤 5：运行项目规定的完整验证**

运行：`python manage.py test core`、`node --test frontend_tests/*.test.mjs`、`bash deployment/tests/run-all.bash`。

预期：现有与新增测试全部通过；如安装脚本集成环境缺少 apt/root，则记录该限制并至少运行静态安装脚本用例。

- [ ] **步骤 6：检查变更并提交**

运行：`git diff --check`、`git status --short`、`git diff --stat`，确认只涉及本计划列出的文件及必要的迁移/测试文件。

提交信息：`feat: add AI smart import for text and documents`。

## 计划自检

- 文本、TXT、MD、DOCX、DOC：任务 1 和任务 4。
- NPC、玩家、世界书及混合分类：任务 2 和任务 5。
- 现有字段、开场白警告与内嵌世界书限制：任务 2、任务 4 和任务 5。
- 鉴权、密钥、文件限制、JSON 校验、无副作用预览：任务 3。
- 重名、事务、角色范围引用、确认后提交：任务 4 和任务 5。
- Ubuntu 安装依赖与用户说明：任务 1 和任务 5。
