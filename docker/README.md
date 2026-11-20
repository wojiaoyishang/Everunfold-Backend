# Everunfold 后端部署入口

setup 的“节点控制台”分组可设置启用开关、独立密码和会话密钥，随机生成值均可查看或修改。`/_nodes` 使用后端发布 URL，旧 `nodes_monitor.origin` 不再覆盖发布 URL；HTTPS 反代需在 setup 配置实际代理来源 IP。

前后端统一使用项目根目录的 `setup.sh` 与 `config.yaml`。配置助手在 `deploy/`，不需要进入 `docker/` 运行。完整的逐步教程见[部署教程](../deploy/README.md)。

选中配置项可查看/复制，F4 查看完整执行结果；终端不支持剪贴板时使用“终端复制”。后端导出后自动显示前端导入命令，带空格的路径也可直接复制。

## 一、Docker 从零构建

1. 安装 Docker Engine、Compose v2、Bash 4+ 和 Git，确认 `docker info` 可用。
2. 克隆本仓库，在仓库根目录执行下列命令。后端需要事先准备 PostgreSQL 和 Redis；前端需要后端导出的节点连接 Base64。
3. 在配置总览填写代码目录、数据目录、容器名称、主网络、额外网络、端口；前端还可选产物输出目录。助手提供连接检测，保存根目录 `config.yaml`。

```bash
./setup.sh --docker
# 保留“空库自动初始化”为 1，选择“构建并启动”
curl --fail http://127.0.0.1:8001/health/ready
```

也可选择新增的 **“初始化数据库并构建启动”**，等价命令为 `./setup.sh --docker init-deploy`：构建一次、显式初始化空库、复检并启动，不受自动初始化开关限制。已初始化的库跳过，不清空数据或重设账号。

已有配置可直接执行 `./setup.sh --docker deploy`。空库自动初始化默认开启，检查连接不会初始化。手动“初始化新库”成功后会提示启动命令；已有库不会重新初始化或自动升级。

工具镜像自动构建，退出即删除工具容器；应用镜像的构建日志实时显示。`start` 和容器重启不会重新编译前端。镜像、挂载、持久化、HTTPS 和首次同步的完整说明见[Docker 从零构建与部署](../deploy/README.md#一docker-从零构建与部署)。

助手明确传入 `CONFIG_FILE=/config/config.yaml`，读取宿主所选 YAML，不再要求 `/data/config.toml`。例如代码 `/opt/everunfold/Everunfold-Backend`、数据 `/opt/everunfold/Everunfold-Data`、主网络 `bridge`、额外网络 `1panel-network`、宿主端口 `3434`、容器端口 `8001`，均可在向导填写。脚本先接入全部网络再启动，不会在同一条创建命令混用两种网络模式。更新后需重新构建后端镜像。

此目录保留 Dockerfile、Compose、入口脚本和一次性工具镜像的构建文件。数据默认位于 `docker/data`，配置来自根目录 YAML。不要手动维护自动生成的 `.env` 和运行配置。

推荐两端加入 `1panel-network`，设置别名 `everunfold-backend`、`everunfold-frontend`，使用容器内部端口 `8001` / `3000` 互访，并在两端的内部通信来源字段填写 `1panel-network`。宿主反代通过该网桥访问时，“HTTPS 反向代理的直接来源 IP”也可填这个名称。也可开启 `host.docker.internal` 映射，此时使用宿主映射端口，例如 `3434`。详见[容器互通教程](../deploy/README.md#容器互通推荐共享网络和别名)。本轮前端更新需重新 `build` 主体与镜像，再 `start`；后端更新后执行 `deploy`。日常重启不编译。

## 二、不使用 Docker

安装 uv；前端另外需要 Node.js 22 和 npm 10。仍在项目根目录运行：

```bash
./setup.sh --native
```

与 Docker 使用同一份根目录 `config.yaml`，无需重新抄写密钥。依赖安装、初始化、编译、启动与长期托管见[不使用 Docker](../deploy/README.md#二不使用-docker)。

“仅保存”成功后保留界面。后端“登记并导出 Base64”后也保留界面，显示完整编码和前端导入命令。

自定义配置路径：先 `export CONFIG_FILE=/宿主绝对目录/config.yaml`，再运行上述 setup 命令。默认只读取项目根 `config.yaml`，无 TOML 自动回退。直接创建容器时使用容器内路径，例如 `-e CONFIG_FILE=/data/config/config.yaml`；挂载其所在目录并确保前端运行用户可写。
