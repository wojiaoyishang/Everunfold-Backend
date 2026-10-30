# Everunfold Backend

Everunfold 博客与轻量 CMS 的独立后端仓库。当前为初始化阶段：FastAPI 可启动，提供进程活性检查与约定的 JSON 响应封装；尚未连接数据库、Redis，未实现业务 API、认证、Worker、Scheduler 或迁移工具。

先读 [文档入口](docs/README.md) 和 [当前交接](docs/handoff/STATUS.md)。原始设计基线及工程计划保存在 `docs/baseline/`，后续开发不依赖临时附件或聊天上下文。

## 本地运行

使用 Python 3.12（64 位标准解释器）和 uv；依赖以 `pyproject.toml` 与 `uv.lock` 为准。在项目根目录运行：

```powershell
uv sync --locked
uv run --locked python -m uvicorn app.entrypoints.api:app --host 127.0.0.1 --port 8000 --reload --no-proxy-headers
```

`uv sync` 创建本项目的 `.venv`。IDE 选择 `.venv\Scripts\python.exe`；需要手动激活时运行 `.\.venv\Scripts\Activate.ps1`，也可直接使用 `uv run` 而无需修改 PowerShell 执行策略。

- 活性检查：`http://127.0.0.1:8000/health/live`，不代表数据库或业务服务就绪。
- 本地交互文档：`http://127.0.0.1:8000/docs`，当前仅用于骨架调试，尚无版本化业务契约。
- `config.example.toml` 是从工程计划提取的规划模板，当前代码**不加载**它；不要以为复制模板即已配置运行依赖。显式配置加载器是下一阶段工作。

## 检查

```powershell
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

GitHub Actions 配置将执行同样检查；远程实际执行结果以 Actions 为准。未添加自动部署。

## 边界

公开业务路径为 `/v1/*`；开发代理 `/api` 不进入后端路由。后续 FastAPI、Celery Worker 与单进程 Scheduler 共用 `app/` 包，但各自独立运行。数据库变更通过显式迁移完成，启动不自动修改 schema。当前没有可运行的 Worker/Scheduler 入口，不使用空实现假装完成。

错误 JSON 不返回输入或异常详情；默认 ASGI 服务器日志尚未配置统一脱敏，生产前必须另行交付结构化日志、敏感信息过滤和诊断关联。当前骨架仅用于本地开发。

WordPress 原始备份保留在仓库外，只读盘点见迁移文档。SQL、原媒体、真实配置、密钥、虚拟环境与日志不提交 Git。前端独立仓库：[Everunfold Frontend](https://github.com/wojiaoyishang/Everunfold-Frontend)。
