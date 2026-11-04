# 容器内开发部署

当前提供 Linux 桌面容器的 Supervisor 与 Nginx 配置，操作和检查见[部署指南](../docs/development/container-deployment.md)。实际 `config.toml`、`.env.container.local` 与 `var/container/` 必须先按实例准备，不进入 Git。

在后端根目录运行：

```bash
./deploy/container-dev.sh start
./deploy/container-dev.sh status
./deploy/container-dev.sh restart api
./deploy/container-dev.sh update
./deploy/container-dev.sh stop
```

`start` 可重复运行；`update` 重新读取 Supervisor 配置；`stop` 关闭本部署自己的监督进程与子进程。默认前端仓库为同级 `Everunfold-Frontend`，可用 `EVERUNFOLD_FRONTEND_ROOT` 覆盖。模板中的运行目录和公开主机占位符由部署者替换；没有自动迁移、导入、生产切换或远程发布。
