# Everunfold 后端 Docker 部署手册

先部署后端，再按前端手册接入节点。本手册沿用已有的 PostgreSQL 和 Redis。以下命令都在**后端仓库的 `docker/` 目录**执行。

## 1. 准备网络和配置文件

```bash
docker network inspect everunfold >/dev/null 2>&1 || docker network create everunfold
mkdir -p instance
cp -n ../config.example.toml instance/config.toml
cp -n ../frontend-nodes.example.toml instance/frontend-nodes.toml
cp -n runtime.env.example runtime.env
```

编辑这三个文件：

| 文件                           | 填写内容                                                                                                                                                                                                        |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime.env`                  | 取消注释并填写实际的 `DATABASE_URL`、`REDIS_URL`、`CELERY_BROKER_URL`，以及应用主密钥、TOTP 主密钥和两个方向的节点令牌。已有站点沿用原密钥。新站点初始化时，还需填写 `EVERUNFOLD_ADMIN_PASSWORD`。              |
| `instance/config.toml`         | 把 `BACKEND_BASE_URL` 改为浏览器访问的公开 API 地址，将公开前端地址加入 `[cors].allowed_origins`。启用根级 `frontend_nodes_file = "./frontend-nodes.toml"`，按实际连接配置 `[internal_auth].allowed_peer_ips`。 |
| `instance/frontend-nodes.toml` | 在 `[frontends.web-main]` 下填写公开前端 `base_url`。前端接收器可用后，填写部署网络可达的受限 HTTPS `settings_receiver_url`，并将 `enabled` 设为 `true`。两项 `*_token_env` 填环境变量名。                      |

数据库和 Redis 地址必须从容器内可达。容器里的 `127.0.0.1` 指本容器。来源白名单填服务实际看到的 socket IP 或 CIDR。默认只有本机能通过宿主端口 8001 访问 API，公开流量通过 HTTPS 代理接入。

## 2. 将上传文件放到持久卷

在 `instance/config.toml` 的现有 `[media]` 小节中修改 `uploads_path`：

```toml
[media]
uploads_path = "/data/uploads"
```

Compose 将名为 `media` 的卷挂到 `/data/uploads`。源码挂在只读的 `/workspace`。后端以 UID/GID 1000 运行，媒体目录需要可写，宿主源码和实例配置需要可读。已有站点迁移时，将原 uploads 目录的内容完整导入此卷，保留相对路径和写入权限，并恢复与之匹配的数据库。

## 3. 构建、初始化或升级数据库

```bash
docker compose config --quiet
docker compose build backend
docker compose run --rm backend alembic -c migrations/alembic.ini current
```

**新站点**使用已创建的空 PostgreSQL 库，执行初始化：

```bash
docker compose run --rm backend python init.database.py --config /etc/everunfold/config.toml --no-create-database
```

**已有站点**先备份数据库、媒体和密钥。迁移版本落后时，在维护窗口停止旧后端并执行：

```bash
docker compose stop backend
docker compose run --rm backend alembic -c migrations/alembic.ini upgrade head
```

首次部署或插件清单变更时，根据下方命令检查并登记后端插件。

## 4. 启动并验收

```bash
docker compose up -d backend
docker compose ps
curl -fsS http://127.0.0.1:8001/health/ready
docker compose logs --tail=100 backend
```

就绪接口应返回 HTTP 200，`database`、`redis`、`schema` 均为 `ok`。再从日志或后台运行记录确认 Celery Worker 和 Scheduler 正常，保持每个运行作用域一个 Scheduler。前端首次部署需要后端数据库中的来源 UUID，用 SQL 查询：

```sql
SELECT source_epoch FROM site_settings WHERE id = 1;
```

## 5. 日常重启和备份

```bash
docker compose restart backend
```

修改环境变量、挂载或节点配置后，执行 `docker compose up -d --force-recreate backend`。常规重启和重建会保留上传卷。日常停机用 `docker compose stop backend`，或用 `docker compose down` 移除容器。带 `-v` 的 `down` 会删除媒体卷。

备份时在同一个停写窗口备份 PostgreSQL、上传卷、实例配置和原密钥。停写后可从本目录导出上传卷，随后确认归档命令成功并检查文件：

```bash
docker compose run --rm -T backend tar -C /data/uploads -czf - . > uploads.tar.gz
tar -tzf uploads.tar.gz
```

## 运行环境变量

| 变量                         | 默认值 | 用途                            |
| ---------------------------- | ------ | ------------------------------- |
| `API_WORKERS`                | `1`    | Uvicorn 进程数                  |
| `WORKER_POOL`                | `solo` | `solo` 或 `prefork`             |
| `WORKER_CONCURRENCY`         | `1`    | Worker 并发。使用 `solo` 时为 1 |
| `WORKER_PREFETCH_MULTIPLIER` | `1`    | 每个 Worker 槽位预取倍数        |
| `WORKER_LOG_LEVEL`           | `INFO` | Worker 日志级别                 |
| `API_SHUTDOWN_TIMEOUT`       | `30`   | API 优雅退出等待秒数            |

`BACKEND_API_PORT` 和 `BACKEND_PUBLISH_PORT` 在 Compose 环境中分别控制容器与宿主端口，默认为 8001。外部网络名可通过 `EVERUNFOLD_NETWORK` 设置。默认发布地址为 `127.0.0.1`。一次部署保持一个 Scheduler，修改运行参数后用 `docker compose up -d --force-recreate backend` 重新加载。默认停机等待 130 秒，长任务按部署需求调整 `BACKEND_STOP_GRACE_PERIOD`。

## 6. 更新主体或插件

- 源码变更使用新的宿主检出版本或维护窗口。可用 Compose 环境中的 `BACKEND_SOURCE_DIR` 指向新的独立检出。按需显式迁移，重新创建后端容器，使三个进程加载同一份代码。Python 依赖清单变更时先重新构建镜像。
- 后端插件放在挂载源码的 `app/plugins/<目录>`。先预检，审核后执行带 `--apply` 的登记命令，再重启三个进程。插件自己的迁移按插件说明显式执行。

```bash
docker compose run --rm backend python -m tools.register_backend_plugins --config /etc/everunfold/config.toml
docker compose run --rm backend python -m tools.register_backend_plugins --config /etc/everunfold/config.toml --apply
```
