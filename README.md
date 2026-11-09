# Everunfold Backend

页面源码/可信注册、独立实时预览与私有元数据已接通，见[页面指南](docs/development/pages.md)和[验收](docs/handoff/pages-live-preview-verification.md)。当前数据库head为0024，页面草稿与公开应用分离。

新 Docker 设备已提供[容器开发部署指南](docs/development/container-deployment.md)：当前本地 HTTP 调试入口为 `http://127.0.0.1:3000`，API 8001 与其他服务由独立 Supervisor 管理。下方旧本地环境说明保留供参考，当前状态以最新交接为准。

当前功能联调仅展示新后台发布的测试文章/说说；旧 WordPress 试导入内容已撤回。开发工具与恢复边界见 [内容管理指南](docs/development/content-management.md)。

Everunfold 博客与轻量 CMS 的独立后端仓库。已实现公开阅读、匿名历史评论点赞、Cookie Session/CSRF、账号资料/密码/会话、用户管理/细分授权、独立受限头像库、API Key与应用签名查询/回调。独立前端接入设置、媒体、多语言文章/说说、协同、版本分支/合并及发布/撤回；设置与内容共用独立Worker/Scheduler/Node接收链路。完整生产缓存体系及其余CMS能力仍按阶段推进，账号使用见[指南](docs/development/accounts.md)。

验证码的配置、全局/单篇规则及扩展接口见[指南](docs/development/captcha.md)。后端可信插件源码统一位于 `app/plugins/`，只规定 `plugin.py` 入口；当前包含基础短代码、卡片/标签示例及 Google/Turnstile 校验适配器。函数/类注册接口及显式部署命令见[后端插件开发](docs/development/backend-plugins.md)。

先读 [文档入口](docs/README.md) 和 [当前交接](docs/handoff/STATUS.md)。原始设计基线及工程计划保存在 `docs/baseline/`，后续开发不依赖临时附件或聊天上下文。

## 本地运行

使用 Python 3.12（64 位标准解释器）和 uv；依赖以 `pyproject.toml` 与 `uv.lock` 为准。在项目根目录运行：

```powershell
uv sync --locked
uv run --locked python -m uvicorn app.entrypoints.api:app --host 127.0.0.1 --port 8001 --reload --no-proxy-headers
```

`uv sync` 创建本项目的 `.venv`。IDE 选择 `.venv\Scripts\python.exe`；需要手动激活时运行 `.\.venv\Scripts\Activate.ps1`，也可直接使用 `uv run` 而无需修改 PowerShell 执行策略。

- 本机 `config.toml` 已填写并被 Git 忽略；新环境按 `config.example.toml` 建立配置，填写专用数据库/Redis 地址。加载器只读取实例文件，不回退示例、不自动搜索 `.env`，明确覆盖项见配置模块。
- 活性检查：`http://127.0.0.1:8001/health/live`；就绪检查：`http://127.0.0.1:8001/health/ready`，分别报告数据库、Redis 和迁移版本。
- 交互文档：`http://127.0.0.1:8001/docs`。公开内容快照契约 `0.7.0`（兼容旧 0.1–0.6）见 `contracts/`，应用版本为 `0.2.0`，二者分别维护。
- 其他项目使用 `8000`，本项目按用户要求统一使用 `8001`。

## 数据准备与前端联调

数据库使用专用 `everunfold_dev`。从空环境开始时，在项目根显式运行：

```powershell
uv run --locked python init.database.py --config config.toml
```

该入口在目标库不存在时尝试创建数据库，显式执行已有Alembic迁移，仅在用户名缺失时创建管理员。未提供`EVERUNFOLD_ADMIN_PASSWORD`时的默认凭据只供首次本地开发；部署应提供其他密码。重复执行不会清库、重置密码或提升账号。账号中心已支持改密，后台支持创建用户与重置临时密码。

只检查或执行已有迁移时仍可显式运行：

```powershell
uv run --locked alembic -c migrations/alembic.ini upgrade head
```

本机 `everunfold_dev` 已显式升级到 `0012_content_access`，保留原公开内容、评论、账号、设置与媒体；四份内容示例由独立 seed 命令添加并已通过发布接口联调。发布/同步启动与操作见 [内容管理指南](docs/development/content-management.md)。完整提取、导入、刷新前端快照和手动验收步骤见 [公开阅读运行指南](docs/development/public-reading.md) 与 [导入工具](tools/wordpress_import/README.md)。原 SQL、公开内容快照、文章媒体和实际配置不进入 Git，新克隆需自行准备数据。

## 检查

```powershell
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

普通测试不连接开发库；真实集成测试必须使用已迁移的独立 `everunfold_test`，本机配置为被忽略的 `config.test.local.toml`：

```powershell
uv run python init.database.py --config config.test.local.toml --no-create-database
$env:RUN_DATABASE_TESTS = "1"
uv run --locked pytest
Remove-Item Env:RUN_DATABASE_TESTS
```

测试会拒绝开发库并使用专用测试库；认证初始化测试还会创建并删除名称受控的临时 PostgreSQL 数据库。当前启用测试库后的完整检查为 `118 passed`，包含双 Uvicorn 的 Cookie/CSRF/共享会话、媒体与内容并发事务，以及独立 Worker/Scheduler/Node 接收器的设置事务、崩溃重投和恢复检查。认证契约导出为 `uv run --locked python -m tools.export_auth_contract`，公开 OpenAPI 导出为 `uv run --locked python -m tools.export_contracts`。

GitHub Actions 配置将执行同样检查；远程实际执行结果以 Actions 为准。未添加自动部署。

## 边界

公开业务路径为 `/v1/*`；开发代理 `/api` 不进入后端路由。`app/modules/content/` 与 `app/modules/auth/` 按模型、DTO、数据库访问、用例服务和路由分责，由静态 Manifest 与唯一 `router_init(ctx)` 装配。FastAPI、Celery Worker 与单进程 Scheduler 共用基础包并分别提供独立入口。数据库启动不自动迁移；当前设置模块已有独立 Worker/Scheduler 和专用 Node 设置接收器。文章/说说多块与属性编辑、媒体管理、正式发布和完整快照分发已接通；密码/定时开放见 ADR 0034，完整生产增量缓存与协同仍后置。

错误 JSON 不返回输入或异常详情；默认 ASGI 服务器日志尚未配置统一脱敏，生产前必须另行交付结构化日志、敏感信息过滤和诊断关联。当前骨架仅用于本地开发。

WordPress 原始备份保留在仓库外，只读盘点见迁移文档。SQL、原媒体、真实配置、密钥、虚拟环境与日志不提交 Git。前端独立仓库：[Everunfold Frontend](https://github.com/wojiaoyishang/Everunfold-Frontend)。

基本设置入口、保存端点和额外进程启动见 [设置运行指南](docs/development/settings.md)，实际多进程/离线验证见 [设置验证记录](docs/handoff/settings-verification.md)。

注册式文章/说说基础草稿管理已接通；迁移 0009、示例填充与开发边界见 [内容管理](docs/development/content-management.md)。

最新编辑器支持顶置标题、根级固定链接、多个 Markdown/HTML 块、分类/标签/封面/SEO/评论、文章密码与定时开放；当前版本与逐项验证见 [编辑工作台验收](docs/handoff/editor-workspace-verification.md)。
