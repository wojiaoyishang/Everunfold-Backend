# Everunfold Backend

Everunfold 博客与轻量 CMS 的独立后端仓库。当前已完成公开阅读基础、真实匿名历史评论点赞，以及基础管理员登录所需的后端能力：PostgreSQL 用户与可撤销 Session、HttpOnly Cookie、CSRF/精确 Origin、管理员身份复核和显式数据库初始化。独立前端已接入登录与主题化空后台；后台文章/媒体/用户管理写接口、Worker、Scheduler 与协同编辑仍未实现。

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
- 交互文档：`http://127.0.0.1:8001/docs`。公开业务契约 `0.1.0` 见 `contracts/`，应用版本为 `0.2.0`，二者分别维护。
- 其他项目使用 `8000`，本项目按用户要求统一使用 `8001`。

## 数据准备与前端联调

数据库使用专用 `everunfold_dev`。从空环境开始时，在项目根显式运行：

```powershell
uv run --locked python init.database.py --config config.toml
```

该入口在目标库不存在时尝试创建数据库，随后显式运行已有 Alembic 迁移，并只在用户名缺失时创建初始管理员。未提供 `EVERUNFOLD_ADMIN_PASSWORD` 时使用用户指定的默认 `admin` / `123456`；重复执行不会清库、改已有密码、改角色或提升已有账号。默认密码只可用于首次本地开发，部署到可访问环境前必须改用其他初始密码；当前尚未提供改密界面。

只检查或执行已有迁移时仍可显式运行：

```powershell
uv run --locked alembic -c migrations/alembic.ini upgrade head
```

本机 `everunfold_dev` 已从迁移 `0002_comment_likes` 显式升级到 `0003_auth_sessions`，原 87 条内容与 11 条评论保持不变，并已创建缺失的默认管理员。完整提取、导入、刷新前端快照和手动验收步骤见 [公开阅读运行指南](docs/development/public-reading.md) 与 [导入工具](tools/wordpress_import/README.md)。原 SQL、公开内容快照、文章媒体和实际配置不进入 Git，新克隆需自行准备数据。

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
$env:RUN_DATABASE_TESTS = "1"
uv run --locked pytest
Remove-Item Env:RUN_DATABASE_TESTS
```

测试会拒绝开发库并使用专用测试库；认证初始化测试还会创建并删除名称受控的临时 PostgreSQL 数据库。当前交接记录的最终结果是默认测试 `45 passed, 12 skipped`、启用测试库后 `57 passed`，包含真实双 Uvicorn 进程的跨源 Cookie/CSRF 与 Session 共享/撤销检查。认证契约导出为 `uv run --locked python -m tools.export_auth_contract`，公开 OpenAPI 导出为 `uv run --locked python -m tools.export_contracts`。

GitHub Actions 配置将执行同样检查；远程实际执行结果以 Actions 为准。未添加自动部署。

## 边界

公开业务路径为 `/v1/*`；开发代理 `/api` 不进入后端路由。`app/modules/content/` 与 `app/modules/auth/` 按模型、DTO、数据库访问、用例服务和路由分责，由静态 Manifest 与唯一 `router_init(ctx)` 装配。未来 FastAPI、Celery Worker 与单进程 Scheduler 共用基础包但独立运行。数据库启动不自动迁移；当前没有可运行的 Worker/Scheduler、后台业务 CRUD 或内部缓存写入口。

错误 JSON 不返回输入或异常详情；默认 ASGI 服务器日志尚未配置统一脱敏，生产前必须另行交付结构化日志、敏感信息过滤和诊断关联。当前骨架仅用于本地开发。

WordPress 原始备份保留在仓库外，只读盘点见迁移文档。SQL、原媒体、真实配置、密钥、虚拟环境与日志不提交 Git。前端独立仓库：[Everunfold Frontend](https://github.com/wojiaoyishang/Everunfold-Frontend)。
