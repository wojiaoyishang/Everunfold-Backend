# 后端 Docker 部署

镜像包含 Python 和锁定依赖，服务从只读挂载的 `/workspace` 加载代码。API、Celery Worker 和 Scheduler 是三个独立进程，由 `serve` 统一管理。进程异常退出时容器停止，由 Compose 重启。运行配置挂载到 `/etc/everunfold`，媒体独立持久化到 `/data/uploads`。沿用现有 PostgreSQL 和 Redis。

## 第一次启动

1. 从仓库根目录进入 `docker/`。如前端或数据库部署已经创建共享网络，复用同一个网络。新建时执行 `docker network create everunfold`。现有 PostgreSQL、Redis 与内部 HTTPS 入口需要能在部署网络上访问。
2. 将 `runtime.env.example` 复制为 `runtime.env`，填入现有数据库、Redis、Broker 和所用密钥变量。将仓库的 `config.example.toml` 复制为 `instance/config.toml` 后按实例填写。填写 `[media].uploads_path = "/data/uploads"`。迁移已有实例时把原媒体无损复制到该挂载，继续使用原应用主密钥与 TOTP 主密钥。
3. 设置公开 API 地址、精确 CORS、节点 ID、双向令牌及真实 socket 来源白名单。跨容器的内部凭据使用 HTTPS，将后端节点配置的 `settings_receiver_url` 指向受限的接收器入口。Compose 服务名仅供 Docker 网络寻址。生产入口用反向代理处理 TLS。
4. 构建镜像并显式检查数据库版本。空库用仓库初始化命令，已有库备份后按部署计划升级。

```bash
docker compose build
docker compose run --rm backend alembic -c migrations/alembic.ini current
# 新实例，目标数据库已创建
docker compose run --rm backend python init.database.py --config /etc/everunfold/config.toml --no-create-database
# 已有实例需要升级时
docker compose run --rm backend alembic -c migrations/alembic.ini upgrade head
docker compose up -d
curl -fsS http://127.0.0.1:8001/health/ready
```

初始化与升级按现有实例状态选择其一。就绪接口检查数据库、运行 Redis 和迁移头。Worker 与 Scheduler 是否成功要另看 `docker compose logs backend` 和后台运行记录。

两套镜像默认使用 UID/GID 1000 跑服务。宿主挂载的源码与实例目录需要对该用户可读，导入已有媒体时也要保留正确的写入权限。

## 运行环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `API_WORKERS` | `1` | Uvicorn 进程数 |
| `WORKER_POOL` | `solo` | `solo` 或 `prefork` |
| `WORKER_CONCURRENCY` | `1` | Worker 并发。使用 `solo` 时为 1 |
| `WORKER_PREFETCH_MULTIPLIER` | `1` | 每个 Worker 槽位预取倍数 |
| `WORKER_LOG_LEVEL` | `INFO` | Worker 日志级别 |
| `API_SHUTDOWN_TIMEOUT` | `30` | API 优雅退出等待秒数 |

`BACKEND_API_PORT` 和 `BACKEND_PUBLISH_PORT` 在 Compose 环境中分别控制容器与宿主端口，默认为 8001。外部网络名可通过 `EVERUNFOLD_NETWORK` 设置。默认发布地址为 `127.0.0.1`。一次部署保持一个 Scheduler，修改运行参数后用 `docker compose up -d --force-recreate backend` 重新加载。默认停机等待 130 秒，长任务按部署需求调整 `BACKEND_STOP_GRACE_PERIOD`。

## 更新主体或插件

- 源码变更使用新的宿主检出版本或维护窗口。可用 Compose 环境中的 `BACKEND_SOURCE_DIR` 指向新的独立检出。按需显式迁移，重新创建后端容器，使三个进程加载同一份代码。Python 依赖清单变更时先重新构建镜像。
- 后端插件放在挂载源码的 `app/plugins/<目录>`。先预检，审核后执行带 `--apply` 的登记命令，再重启三个进程。插件自己的迁移按插件说明显式执行。

```bash
docker compose run --rm backend python -m tools.register_backend_plugins --config /etc/everunfold/config.toml
docker compose run --rm backend python -m tools.register_backend_plugins --config /etc/everunfold/config.toml --apply
```
