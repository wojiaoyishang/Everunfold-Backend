# Everunfold Backend

Everunfold 博客与轻量 CMS 的独立后端仓库。当前已完成公开阅读基础：严格配置、PostgreSQL 模型与显式迁移、Redis/数据库就绪检查、公开内容/分类/搜索/评论读取，以及 WordPress 公开数据导入和快照导出。当前没有后台写接口、认证、Worker、Scheduler 或协同编辑。

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

数据库使用专用 `everunfold_dev`；显式迁移命令为：

```powershell
uv run --locked alembic -c migrations/alembic.ini upgrade head
```

本机已经迁移并导入公开内容。完整提取、导入、刷新前端快照和手动验收步骤见 [公开阅读运行指南](docs/development/public-reading.md) 与 [导入工具](tools/wordpress_import/README.md)。原 SQL、公开内容快照、文章媒体和实际配置不进入 Git，新克隆需自行准备数据。

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

测试会拒绝开发库并在事务中回滚。当前实际运行 30 项测试全部通过，包含导入工具测试。重新导出公开 API：`uv run --locked python -m tools.export_contracts`。

GitHub Actions 配置将执行同样检查；远程实际执行结果以 Actions 为准。未添加自动部署。

## 边界

公开业务路径为 `/v1/*`；开发代理 `/api` 不进入后端路由。`app/modules/content/` 按模型、DTO、数据库访问、用例服务和路由分责，由静态 Manifest 与唯一 `router_init(ctx)` 装配。未来 FastAPI、Celery Worker 与单进程 Scheduler 共用基础包但独立运行。数据库启动不自动迁移；当前没有可运行的 Worker/Scheduler 或内部缓存写入口。

错误 JSON 不返回输入或异常详情；默认 ASGI 服务器日志尚未配置统一脱敏，生产前必须另行交付结构化日志、敏感信息过滤和诊断关联。当前骨架仅用于本地开发。

WordPress 原始备份保留在仓库外，只读盘点见迁移文档。SQL、原媒体、真实配置、密钥、虚拟环境与日志不提交 Git。前端独立仓库：[Everunfold Frontend](https://github.com/wojiaoyishang/Everunfold-Frontend)。
