# 容器内开发部署

当前提供 Linux 桌面容器的 Supervisor 与 Nginx 配置，操作和检查见[部署指南](../docs/development/container-deployment.md)。实际 `config.toml`、`.env.container.local` 与 `var/container/` 必须先按实例准备，不进入 Git。

当前容器的日常启动入口为 `/workspace/start-everunfold.sh`，它通过相对软链接调用本目录的 `start-test.sh`。在任意工作目录执行即可启动既有服务、等待六进程与HTTP就绪，并检查四个端口绑定0.0.0.0；已运行的服务不重启。`--check`只检查当前状态；`--help`查看用法。默认等待120秒，可用`EVERUNFOLD_START_TIMEOUT`设置1至600秒。并发启动通过运行目录的文件锁串行保护，锁不传给新启动的Supervisor。

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
