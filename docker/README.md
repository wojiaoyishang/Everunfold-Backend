# Everunfold 后端部署入口

前后端统一使用项目根目录的 `setup.sh` 与 `config.yaml`。配置助手在 `deploy/`，不需要进入 `docker/` 运行。完整的逐步教程见[部署教程](../deploy/README.md)。

## 一、Docker 从零构建

1. 安装 Docker Engine、Compose v2、Bash 4+ 和 Git，确认 `docker info` 可用。
2. 克隆本仓库，在仓库根目录执行下列命令。后端需要事先准备 PostgreSQL 和 Redis；前端需要后端导出的节点连接 JSON。
3. 在配置总览填写代码目录、数据目录、容器名称、主网络、额外网络、端口；前端还可选产物输出目录。助手提供连接检测，保存根目录 `config.yaml`。

```bash
./setup.sh --docker
# 保留“空库自动初始化”为 1，选择“构建并启动”
curl --fail http://127.0.0.1:8001/health/ready
```

已有配置可直接执行 `./setup.sh --docker deploy`。空库自动初始化默认开启，检查连接不会初始化。手动“初始化新库”成功后会提示启动命令；已有库不会重新初始化或自动升级。

工具镜像自动构建，退出即删除工具容器；应用镜像的构建日志实时显示。`start` 和容器重启不会重新编译前端。镜像、挂载、持久化、HTTPS 和首次同步的完整说明见[Docker 从零构建与部署](../deploy/README.md#一docker-从零构建与部署)。

后端直接读取 `/workspace/config.yaml`，不再要求 `/data/config.toml`。例如代码 `/opt/everunfold/Everunfold-Backend`、数据 `/opt/everunfold/Everunfold-Data`、主网络 `bridge`、额外网络 `1panel-network`、宿主端口 `3434`、容器端口 `8001`，均可在向导填写。脚本先接入全部网络再启动，不会在同一条创建命令混用两种网络模式。更新后需重新构建后端镜像。

此目录保留 Dockerfile、Compose、入口脚本和一次性工具镜像的构建文件。数据默认位于 `docker/data`，配置来自根目录 YAML。不要手动维护自动生成的 `.env` 和运行配置。

## 二、不使用 Docker

安装 uv；前端另外需要 Node.js 22 和 npm 10。仍在项目根目录运行：

```bash
./setup.sh --native
```

与 Docker 使用同一份根目录 `config.yaml`，无需重新抄写密钥。依赖安装、初始化、编译、启动与长期托管见[不使用 Docker](../deploy/README.md#二不使用-docker)。
