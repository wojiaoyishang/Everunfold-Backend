# 后端初始化验证记录

日期：2026-10-30，Windows / PowerShell，项目根 `E:\Programming\Python\Everunfold-Backend`。这里只记录本轮骨架检查，不是完整业务或生产验收。

## 实际执行

| 检查 | 结果 |
| --- | --- |
| 项目内虚拟环境 | `.venv\Scripts\python.exe`，CPython 3.12.4，已创建并安装锁定依赖 |
| `uv sync --locked` | 通过，环境与锁文件同步，移除不再使用的旧测试客户端依赖 |
| `uv lock --check` | 通过，31 个解析包（含项目和平台条件包） |
| `uv run --locked ruff check .` | 通过 |
| `uv run --locked ruff format --check .` | 通过，原始 `docs/baseline/` 显式排除 |
| `uv run --locked mypy` | 通过，7 个源码文件无问题 |
| 清除当前进程 `PYTHONPATH` 后 `uv run --locked pytest` | 7 项通过，无需全局源码路径配置 |
| 直接导入 ASGI app | 通过，路由仅框架本地文档/OpenAPI与 `/health/live` |
| 两项目配置示例 TOML 语法 | 均通过；没有声称加载器已实现 |
| 公共文件同步 | 10 份公共资料逐字节一致，包含环境和去敏迁移盘点 |
| 原始附件 | 两仓库副本均与用户附件逐字节一致，SHA256 见 `docs/baseline/README.md` |
| 忽略规则 | `.venv`、实际配置、`.env`、`var/` 和源 SQL 均被忽略 |

7 项测试包含：活性不冒充就绪；不存在的普通/业务/开发代理路径保持 404；405 保留状态与 Allow；422/500 响应不回显原始输入或异常；三个响应类的状态、业务码、省略空 data、头部及 JSON 可编码值行为。

首次检查发现并修正：Ruff 当前版本会检查 Markdown 代码块，因此显式排除不可改写的原始基线；测试不再要求未定稿的 UTC 字符串拼写（`Z` 与 `+00:00` 均按时间语义核验）；Starlette 当前 TestClient 使用 [官方推荐的 httpx2](https://starlette.dev/testclient/)，已移除旧 httpx 依赖。没有修改原始基线。

直接依赖：FastAPI `0.162.0`、Uvicorn `0.54.0`；开发依赖：httpx2 `2.16.0`、mypy `2.4.1`、pytest `9.2.0`、Ruff `0.17.3`。全部精确版本、来源与哈希见 `pyproject.toml` / `uv.lock`；uv 工具版本为 `0.9.0`。

## 未执行与限制

- 尝试额外启动 Uvicorn 子进程做真实 HTTP 冒烟时，被工具自动审批策略拒绝；工具未返回具体原因。没有改用其他启动方式绕过，改由 TestClient 验证应用行为，不能据此声称已检查实际网络监听。
- 未执行 PostgreSQL、Redis、Celery、Scheduler、多进程协同/缓存故障、生产跨源 Cookie/CSRF、迁移与恢复验收：这些功能和基础设施尚未实现。
- 错误响应过滤不等于日志已经安全：Starlette 会重新抛出服务异常，默认 Uvicorn 可能记录异常详情；生产日志脱敏/诊断关联仍待交付。
- GitHub Actions 配置已建立；本地通过不能代替远端 Actions 的实际运行结论，远端状态以仓库检查为准。
- 线上博客网页读取超时，当前无线上视觉审阅结果；本步只需要骨架，主题设计另行开展。

## 提交与复查

目标为 `main` → 用户指定仓库，使用已认证的同账号 SSH。两远程在初始化前均无引用，不覆盖已有历史。推送及当前工作区状态以实际 Git 记录为准。

提交前核对：暂存清单不含虚拟环境/依赖、备份/媒体、真实配置或密钥；原件通过 `.gitattributes` 的 `-text` 规则保留字节，并以源文件 Git blob 哈希与暂存 blob 比对；除原件外检查新增文件空白和仓库内 Markdown 链接。后续接手运行根 README 的检查命令即可复现本地范围。
