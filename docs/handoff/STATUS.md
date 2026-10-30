# 后端交接状态

更新：2026-10-30（Asia/Shanghai）。阶段：**第 0 步，基线对齐与项目初始化**。本仓库是独立 Git 项目；当前不连接 WordPress 或生产服务。

## 恢复上下文

1. 读本页、[文档索引](../README.md)、[开发协议](../development/protocol.md) 和 [约束](../architecture/constraints.md)。
2. 查看 `git status`、当前分支/提交与 `origin`；保留已有修改。远程同步情况用实际 Git/GitHub 状态核对，不依赖聊天。
3. 原始附件在 `docs/baseline/`，哈希与维护规则在该目录 README；历史聊天只作辅助。确认需求、工程提案和实际代码分别判断。
4. 查看 [本轮验证记录](bootstrap-verification.md) 以及根 README 的复现命令。

## 已完成

- 对齐设计 v2.11 与工程 v0.14，阅读相关历史聊天，建立逐字节原件副本、来源/哈希、提案与已确认边界说明。
- 建立 `AGENTS.md`、开发协议、ADR 0001、文档分目录和双仓库公共资料同步规则。
- Python 3.12 标准解释器 + 项目内 `.venv`，uv 管理独立锁文件；IDE 可直接选择 `.venv\Scripts\python.exe`。
- 根包 `app/`、FastAPI `app.entrypoints.api:app` 和 `create_app()`；仅提供 `/health/live` 活性检查。
- `JSONResponseSuccess` / `JSONResponseFail` / `JSONResponseError` 及 HTTP/校验/异常 JSON 入口：真实状态码、None 数据省略、错误响应不回显敏感输入。
- Ruff、mypy、pytest 检查配置和 GitHub Actions；CI 不做部署。
- `config.example.toml` 从原文提取，仅是**未加载的规划模板**。真实实例配置、密钥、运行数据、环境和备份均忽略提交。
- WordPress 只读盘点及后续迁移步骤见 [清点记录](../migration/wordpress-inventory.md)，没有执行导入。

应用版本 `0.1.0` 是骨架包版本，不是 API/WS/ICP 已发布的兼容契约版本。直接依赖 FastAPI `0.162.0`、Uvicorn `0.54.0`；开发工具和所有传递依赖以 `pyproject.toml`、`uv.lock` 为准。

## 未实现与限制

- 配置加载器/Schema/环境覆盖/令牌解析、PostgreSQL/SQLAlchemy/Alembic、Redis、Session/权限、i18n、静态 Manifest 加载器均未实现。
- 没有可运行的 Celery Worker 或 Scheduler，未投递任何任务；不能运行原文中的规划命令并认为服务存在。
- 尚无正式模型、迁移、业务端点、业务错误码、WS/ICP Schema、发布/审核/协同、搜索、媒体管理、缓存同步和 WordPress 导入器。
- `/health/live` 仅反映进程活性；没有 `/health/ready`，没有数据库/队列就绪判断。尚未实施生产日志脱敏，默认 ASGI 服务器可能记录完整异常；当前只用于本地骨架开发。
- 没有导入原备份或执行 PHP，没有修改线上站点，没有生产部署、恢复演练或完整 P0 验收。
- 额外启动后端子进程做 HTTP 冒烟的动作被工具自动审批策略拒绝；未绕过，采用应用内 TestClient 检查，具体结果见验证记录。

## 建议下一步

1. 交付配置加载器设计与实现：定义根定位、`CONFIG_FILE`、显式覆盖表、严格校验/错误、密钥引用和脱敏；按 v0.14 建议写对应 ADR 与测试，不把示例值直接当生产默认。
2. 在 `docs/database/` 设计核心实体、主键、约束及内容版本事务，落地 SQLAlchemy 模型和 Alembic 显式迁移；只对隔离数据库验证，启动不得迁移。
3. 在 `contracts/` 发布第一个明确版本的 API/错误码/WS/ICP Schema，前端才据此生成客户端；先守住 `/v1/*` 和服务端配置边界。
4. 后续逐项落实 Worker/Scheduler/Outbox、协同、媒体、缓存与故障测试；实施顺序和 P0 来源见原始工程计划。初始化检查不能替代这些验收。

待定产品/实现细节已列在架构约束尾部，无需重新询问已定架构。下一项只解决其所需的主键、Schema、会话等具体问题；UI 框架、编辑器、语言清单和中文搜索还没有被冻结。

## Git 与跨仓库

目标分支 `main`；目标 [Everunfold-Backend](https://github.com/wojiaoyishang/Everunfold-Backend)，本机 `origin` 使用同仓库 SSH 地址，原因是现有 SSH 身份可用。前端位置 `E:\Programming\Node\Everunfold-Frontend`，目标 [Everunfold-Frontend](https://github.com/wojiaoyishang/Everunfold-Frontend)。

公共协议/原件按 `docs/baseline/README.md` 同步；环境和本次迁移盘点目前也相同。后端是唯一契约权威，前端生成客户端目录当前仍为空实现。后续每次有意义交付更新本页、实际检查记录和相关 ADR，保持两边兼容范围可追溯。
