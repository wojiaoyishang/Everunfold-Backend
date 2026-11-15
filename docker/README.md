# Everunfold 后端 Docker 部署手册

先部署后端，再按前端手册接入节点。本手册沿用已有的 PostgreSQL 和 Redis。以下命令都在**后端仓库的 `docker/` 目录**执行。

## 1. 准备网络和配置文件

先在终端运行配置向导，需 Python 3.10 或更新版本：

```bash
python3 configure.py
```

向导询问容器监听、宿主发布、PostgreSQL、Redis、主密钥、令牌和 Worker 参数。前端公开地址在 `/_nodes` 管理。新站点可以为每项密钥、令牌和初始管理员密码输入自定义值，留空时独立生成。迁移方式要求填写原值。密码和密钥输入时隐藏，确认后写入私有的 `runtime.env`。向导跳过已存在的配置文件组。如需调整已有站点，直接编辑原文件。

共用 Docker 网络需要单独创建。使用向导后，可跳过下列复制模板的命令。

```bash
docker network inspect everunfold >/dev/null 2>&1 || docker network create everunfold
mkdir -p data/uploads
cp -n ../config.example.toml data/config.toml
cp -n runtime.env.example runtime.env
```

使用手工配置时，编辑以下文件：

| 文件               | 填写内容                                                                                                                                                                                              |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime.env`      | 填写实际的 `DATABASE_URL`、`REDIS_URL`、`CELERY_BROKER_URL` 和所需密钥。已有站点沿用原密钥。新站点初始化前设置 `EVERUNFOLD_ADMIN_PASSWORD`。                                                          |
| `data/config.toml` | 去掉示例的回环 `BACKEND_BASE_URL`，将 `[cors].allowed_origins` 设为空数组，打开 `[cors].use_frontend_registry`。在 `[internal_auth].allowed_peer_ips` 填写 FastAPI 实际看到的前端服务或内部代理来源。 |
| `.env`             | 按需设置 `BACKEND_API_PORT`、`BACKEND_BIND_ADDRESS` 和 `BACKEND_PUBLISH_PORT`，控制内部端口和宿主发布地址。                                                                                           |

数据库和 Redis 地址需从容器内可达。容器里的 `127.0.0.1` 指本容器。默认只向宿主回环地址发布 8001，公开流量由 HTTPS 代理接入。旧站点的显式 `BACKEND_BASE_URL` 和 CORS 白名单继续有效。省略公开 API 根地址时，认证 Cookie 使用 Secure，反代需在各前端公开入口转发 `/media/`，或在后端单独配置 `[media].base_url`。第三方应用的签名查询接入仍需显式设置 `BACKEND_BASE_URL`。

## 2. 指定宿主数据目录

在 `data/config.toml` 的现有 `[media]` 小节中修改 `uploads_path`：

```toml
[media]
uploads_path = "/data/uploads"
```

Compose 挂载两个宿主目录。源码只读挂到 `/workspace`，`docker/data/` 可写挂到 `/data`，包含配置、`uploads/` 和上传临时目录。宿主数据目录可用 Compose 的 `BACKEND_DATA_DIR` 指定。Redis 缓存由既有 Redis 服务管理。后端以 UID/GID 1000 运行，媒体目录需要可写，宿主源码和实例配置需要可读。已有站点迁移时，将原 uploads 目录的内容完整复制到宿主数据目录的 `uploads/`，保留相对路径和写入权限，并恢复与之匹配的数据库。

如果把数据放在仓库外，在 `docker/.env` 设置 `BACKEND_DATA_DIR=/srv/everunfold/backend-data`，并按下列结构准备目录。环境变量和密钥仍放在私有的 `docker/runtime.env`。

```text
/srv/everunfold/backend-data/
├── config.toml
└── uploads/
```

## 3. 构建、初始化或升级数据库

```bash
docker compose config --quiet
docker compose build backend
docker compose run --rm backend alembic -c migrations/alembic.ini current
```

**新站点**使用已创建的空 PostgreSQL 库，执行初始化：

```bash
docker compose run --rm backend python init.database.py --config /data/config.toml --no-create-database
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

## 5. 在 `/_nodes` 登记前端

初始化数据库后，首次启用 PostgreSQL 节点清单：

```bash
docker compose run --rm backend python -m tools.import_frontend_nodes
```

为控制台设置独立密码和会话密钥。执行下列命令得到 `password_hash`，写入 `data/config.toml` 的 `[nodes_monitor]`，启用 `enabled = true`，并将 `origin` 设为实际访问的 HTTPS 运维地址。会话随机值放进 `runtime.env` 的 `EVERUNFOLD_NODES_SESSION_SECRET`。更新后重新创建容器。

```bash
docker compose run --rm backend python -m tools.nodes_monitor_password
openssl rand -hex 32
docker compose up -d --force-recreate backend
```

反向代理终止 TLS 时需保留公开 Host，以固定值覆盖 `X-Forwarded-Proto: https`，并将代理直接连接 API 的实际 IP 加入 `[nodes_monitor].trusted_tls_proxy_ips`。控制台按真实连接 IP 核对。生产环境限制后台端口仅部署网络可达。

每台前端先由部署人员登记内部接收器和令牌变量名，再在 `/_nodes` 添加对应节点，填写公开地址并启用。示例使用 `web-main`：

```bash
docker compose run --rm backend python -m tools.import_frontend_nodes \
  --bind web-main https://receiver.example.com \
  NEXTJS_TO_FASTAPI_WEB_MAIN_TOKEN FASTAPI_TO_NEXTJS_WEB_MAIN_TOKEN
```

内部接收地址填部署网络可访问的 HTTPS 入口，与前端公开地址分别设置。多节点使用不同 ID 和各自的双向令牌，实际令牌值分别注入对应的前后端。启用节点后，公开网址和启停可在控制台更新。启用了 `use_frontend_registry` 时，已绑定且启用的节点公开 Origin 会用于后端 CORS、浏览器写入和协同连接校验。内部地址及令牌引用变更前先停用节点。

## 6. 日常重启和备份

```bash
docker compose restart backend
```

修改环境变量、挂载或节点配置后，执行 `docker compose up -d --force-recreate backend`。日常停机用 `docker compose stop backend`。重启、重建或移除容器后，宿主数据目录仍保留。用过旧 Compose 的站点，切换前应停写并把原 `media` 卷的内容迁入宿主 `data/uploads/`，同时将原 `instance/` 配置复制到 `data/`。

备份时在同一个停写窗口备份 PostgreSQL、宿主数据目录和私有 `runtime.env`。默认布局可直接在宿主归档上传文件：

```bash
tar -C data -czf uploads.tar.gz uploads
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

## 7. 更新主体或插件

- 源码变更使用新的宿主检出版本或维护窗口。可用 Compose 环境中的 `BACKEND_SOURCE_DIR` 指向新的独立检出。按需显式迁移，重新创建后端容器，使三个进程加载同一份代码。Python 依赖清单变更时先重新构建镜像。
- 后端插件放在挂载源码的 `app/plugins/<目录>`。先预检，审核后执行带 `--apply` 的登记命令，再重启三个进程。插件自己的迁移按插件说明显式执行。

```bash
docker compose run --rm backend python -m tools.register_backend_plugins --config /data/config.toml
docker compose run --rm backend python -m tools.register_backend_plugins --config /data/config.toml --apply
```
