# Everunfold 后端 Docker 部署手册

先部署后端，再按前端手册接入节点。本手册沿用已有的 PostgreSQL 和 Redis。以下命令都在**后端仓库的 `docker/` 目录**执行。

## 1. 准备网络和配置文件

先在 POSIX 交互终端运行配置向导，需 Python 3.11 或更新版本及其标准库 `termios`：

```bash
python3 configure.py
```

启动后先显示全部向导配置项、当前值及未填项，再显示菜单：

```text
1.按次序配置  2.修改某配置项  3.退出
```

- 选 `1` 按顺序编辑，结束后返回总览；选 `2` 输入配置项编号，只修改该项。
- 输入框预填当前值，直接回车保留；`←/→` 移动光标，`↑/↓` 调取该项在本次会话中的输入，`Ctrl+U` 清空。程序显式设置终端回显和行编辑属性，正常退出、Ctrl+C 或 EOF 后恢复原设置。历史不写入磁盘。
- PostgreSQL、Redis、Broker URL 在输入和总览中完整回显。独立密钥、令牌及管理员密码在总览中遮盖，进入该项编辑时回显，支持同样的光标和历史操作。
- 选 `3` 后，有修改时选择 `y` 保存并退出、`n` 放弃、`c` 返回。必填项或跨项校验未通过时返回菜单。未修改就退出时不会创建文件。

后端发布 URL、容器监听与宿主映射仍分别配置；多个前端公开地址继续在 `/_nodes` 管理。**“前端内部调用后端的来源 IP/CIDR”默认是 `127.0.0.1, ::1`**，同时要求正确的节点令牌。跨容器调用时，另加后端实际看到的前端服务或内部代理 IP/CIDR；清空此项可关闭内部来源许可。

新站点的主密钥、令牌和初始管理员密码可自定义；尚无值时回车生成，已有值时回车保留，清空后回车重新生成。迁移或已有站点模式要求保留原密钥。再次运行会读取已有配置，自动使用已有站点模式，修改单项无需重填其余内容。

保存前在原文件旁创建 `文件名.<标识>.bak` 备份，文件权限为 `0600`。保留向导未编辑的 TOML 字段和其他环境变量；TOML 会重新排版，原注释保存在备份中。保存期间若文件被其他程序修改，向导拒绝覆盖。支持 `.env` 中的 `BACKEND_DATA_DIR`、`BACKEND_ENV_FILE` 自定义路径；路径本身仍在 `.env` 中调整。

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
| `data/config.toml` | 将 `BACKEND_BASE_URL` 改为后端真实发布 URL。将 `[cors].allowed_origins` 设为空数组，打开 `[cors].use_frontend_registry`。在 `[internal_auth].allowed_peer_ips` 填写 FastAPI 实际看到的前端服务或内部代理来源。 |
| `.env`             | 按需设置 `BACKEND_API_PORT`、`BACKEND_BIND_ADDRESS` 和 `BACKEND_PUBLISH_PORT`，控制容器监听端口和 Docker 宿主端口映射。 |

数据库和 Redis 地址需从容器内可达。容器里的 `127.0.0.1` 指本容器。默认只向宿主回环地址映射 8001，公开流量由 HTTPS 代理接入。已有 CORS 白名单继续有效。

### 三类地址分别配置

| 层次 | 配置位置与示例 | 作用 |
| --- | --- | --- |
| 后端进程监听 | `runtime.env` 的 `API_HOST=0.0.0.0`；`.env` 的 `BACKEND_API_PORT=8001` | Uvicorn 在容器内绑定 `0.0.0.0:8001`；Compose 将端口注入为 `API_PORT` |
| Docker 宿主端口映射 | `.env` 的 `BACKEND_BIND_ADDRESS=127.0.0.1`、`BACKEND_PUBLISH_PORT=18001` | 将宿主 `127.0.0.1:18001` 转发到容器的 `8001` |
| 后端发布 URL | `data/config.toml` 根级 `BACKEND_BASE_URL = "https://api.example.com"` | 生成对外绝对链接，默认媒体地址为 `https://api.example.com/media/...` |

例如外部请求经过 `https://api.example.com` → 宿主反向代理 → `127.0.0.1:18001` → 容器 `0.0.0.0:8001`。三项独立设置，修改发布 URL 不改变进程监听或 Docker 映射。`0.0.0.0` 是监听所有 IPv4 网卡的绑定值，发布 URL 应填写访问者可达的域名或 IP。Docker 映射要求进程监听容器网卡，通常保留 `API_HOST=0.0.0.0`。

`BACKEND_BASE_URL` 填完整根 URL，不带 `/api`、`/v1`、查询参数或片段。生产填写 HTTPS 反代入口，本机调试可以用 `http://127.0.0.1:8001`；认证 Cookie 按发布 URL 的 HTTPS 配置启用 Secure。使用独立媒体域名或 CDN 时，设置 `[media].base_url = "https://cdn.example.com/assets"` 覆盖媒体 URL 前缀，上传仍写入 `/data/uploads`。

**用上一版向导生成过配置的实例**：在 `data/config.toml` 首个 `[小节]` 之前补上 `BACKEND_BASE_URL = "https://你的后端域名"`，再重新创建容器。加载器兼容旧配置缺省时的相对 `/media` 行为，但多前端部署应显式填写发布 URL，避免资源链接落到各前端域名。环境中的同名 `BACKEND_BASE_URL` 优先于 TOML；修改时检查是否仍有旧覆盖值。

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

`API_HOST` 在 `runtime.env` 中控制容器进程绑定 IP，默认为 `0.0.0.0`。Compose 从 `.env` 读取 `BACKEND_API_PORT`，将它同时用于容器 `API_PORT` 和映射目标端口；直接 `docker run` 时设置 `API_PORT`。`BACKEND_PUBLISH_PORT` 控制宿主端口，默认 8001，`BACKEND_BIND_ADDRESS` 控制宿主映射 IP，默认 `127.0.0.1`。健康检查访问容器内部监听地址。外部网络名可通过 `EVERUNFOLD_NETWORK` 设置。一次部署保持一个 Scheduler，修改配置后用 `docker compose up -d --force-recreate backend` 重新加载。默认停机等待 130 秒，长任务按部署需求调整 `BACKEND_STOP_GRACE_PERIOD`。

## 7. 更新主体或插件

- 源码变更使用新的宿主检出版本或维护窗口。可用 Compose 环境中的 `BACKEND_SOURCE_DIR` 指向新的独立检出。按需显式迁移，重新创建后端容器，使三个进程加载同一份代码。Python 依赖清单变更时先重新构建镜像。
- 后端插件放在挂载源码的 `app/plugins/<目录>`。先预检，审核后执行带 `--apply` 的登记命令，再重启三个进程。插件自己的迁移按插件说明显式执行。

```bash
docker compose run --rm backend python -m tools.register_backend_plugins --config /data/config.toml
docker compose run --rm backend python -m tools.register_backend_plugins --config /data/config.toml --apply
```
