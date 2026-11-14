# 容器内开发部署

当前提供 Linux 桌面容器的 Supervisor 与 Nginx 配置，操作和检查见[部署指南](../docs/development/container-deployment.md)。实际 `config.toml`、`.env.container.local` 与 `var/container/` 必须先按实例准备，不进入 Git。

当前容器的日常启动入口为 `/workspace/start-everunfold.sh`，它通过相对软链接调用本目录的 `start-test.sh`。在任意工作目录执行即可启动服务、等待七个进程和 HTTP 就绪，并检查四个开发端口的监听状态。已运行的服务不重启。`--check` 只检查，`--help` 查看用法。默认等待 120 秒，可用 `EVERUNFOLD_START_TIMEOUT` 设置 1 至 600 秒。并发启动通过运行目录的文件锁串行保护，锁不传给新启动的 Supervisor。

开发容器需要 `redis-server`，Supervisor 默认在 `127.0.0.1:6381` 启动专用查询缓存，限用 64 MiB，不持久化。可以在运行目录 `bin/redis-server` 放置可执行文件，也可在启动 Supervisor 前设置 `EVERUNFOLD_QUERY_CACHE_REDIS_BIN`。API 的默认连接可通过 `[query_cache].url` 或 `QUERY_CACHE_REDIS_URL` 覆盖。更新现有容器时先运行 `./deploy/container-dev.sh update` 添加进程，再重启 API 应用连接配置。

如需在同样的目录结构中重建入口：

```bash
ln -s Everunfold/Everunfold-Backend/deploy/start-test.sh /workspace/start-everunfold.sh
/workspace/start-everunfold.sh
```

在后端根目录运行：

```bash
./deploy/container-dev.sh start
./deploy/container-dev.sh status
./deploy/container-dev.sh restart api
./deploy/container-dev.sh update
./deploy/container-dev.sh stop
```

`start` 可重复运行；`update` 重新读取 Supervisor 配置；`stop` 关闭本部署自己的监督进程与子进程。默认前端仓库为同级 `Everunfold-Frontend`，可用 `EVERUNFOLD_FRONTEND_ROOT` 覆盖。模板中的运行目录和公开主机占位符由部署者替换；没有自动迁移、导入、生产切换或远程发布。
