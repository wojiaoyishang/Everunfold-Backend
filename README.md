# Everunfold Backend

Everunfold 博客与轻量 CMS 的后端仓库，使用 Python、FastAPI、PostgreSQL 和 Redis。负责身份、权限、编辑数据、发布、节点同步与后台任务。

配套仓库 [Everunfold 前端](https://github.com/wojiaoyishang/Everunfold-Frontend)。当前整理为预发布版本，配套提交、运行验证和已知限制见[预发布说明](docs/source/release/prerelease.rst)。

## 文档

- [本地快速开始](docs/source/welcome/quickstart.rst) · 初始化、配置和启动两端
- [运行与部署](docs/source/welcome/deployment.rst) · 连接、进程、代理与数据保留
- [后端开发参考](docs/source/backend/index.rst) · 源码、扩展和检查
- [构建与发布文档](docs/source/release/documentation.rst) · Sphinx 本地预览与 GitHub Pages
- [历史设计索引](docs/README.md) · 基线、决策和交接

## 本地检查

Python 3.12、uv、PostgreSQL 和 Redis。在仓库根目录执行：

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked pytest
```

本地 API 使用 `8001`。首次启动前需要创建专用数据库并显式运行初始化及迁移。API、Celery Worker 和 Scheduler 分别启动。 完整步骤见[快速开始](docs/source/welcome/quickstart.rst)。

## 目录

| 路径 | 职责 |
| --- | --- |
| `app/entrypoints/`、`app/core/` | API、Worker、Scheduler 入口和运行基础 |
| `app/modules/`、`app/plugins/` | 业务模块和可信插件 |
| `migrations/`、`contracts/` | Alembic 迁移和版本化接口契约 |
| `tools/`、`tests/` | 部署、契约导出和测试工具 |
| `docs/source/` | 可发布的 RST 手册 |
