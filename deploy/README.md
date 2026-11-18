# Everunfold 后端部署教程

setup 的「监控」分组包含 Prometheus 开关、采集令牌、指标来源和 Loki 查询及写入白名单。保存后生成 `var/monitoring/loki-access.conf`，在监控服务器的 Loki 独立反代站点加载。采集模板见 [Everunfold Deploy 的监控指南](https://github.com/wojiaoyishang/Everunfold-Deploy/tree/main/monitoring)。

三个白名单分别填写访问者实际使用的 IP 或 CIDR；Docker 下也可填已连接的网络名，例如 `1panel-network`。向导会把网络名换算成当前 IPv4/IPv6 网段并保存，重建网络变更子网时重新填写网络名。后端 `/metrics` 的聚合方式和采集令牌沿用现有设置。

选择下面一条路径，从准备环境一直做到启动验收。**所有命令都在后端仓库根目录执行**。前端教程见[配套仓库](https://github.com/wojiaoyishang/Everunfold-Frontend/blob/main/deploy/README.md)。

配置助手先展示全部配置，支持鼠标、顺序配置和按项编辑。新站点密钥自动生成，可查看和修改；已有站点保留原密钥。PostgreSQL 可分项填写，也可编辑完整 URL。连接检测不会初始化或升级数据库。

前后端统一在**各自项目根目录**运行 `./setup.sh`，选择 Docker 或原生部署；也可使用下方显式参数。配置助手代码在 `deploy/`，`docker/` 只保存镜像及容器相关文件。

**根目录 `config.yaml` 是唯一需要维护的部署配置**。首次保存自动生成，权限为 `0600`，已加入 Git 忽略。配置、密钥及部署参数均从这里读取；兼容 TOML、环境文件和 Compose 参数由助手自动生成。手动修改 YAML 后，下一次 `build`、`start` 或 `check` 会应用修改。不要再修改生成的 `config.runtime.toml`、`.env`、`runtime.env`。两端直接读取所选 YAML；`config.runtime.toml` 仅为兼容派生文件。

### 配置文件路径

配置文件路径由**外部环境变量 `CONFIG_FILE`** 指定；未设置时，只读取当前项目根目录的 `config.yaml`，与启动时的工作目录无关。相对路径也相对于项目根目录。指定的文件缺失或无效会报错，不再自动寻找另一份 TOML。setup 和应用使用同一份 YAML，页面及启动日志会显示最终路径。

```bash
# 默认：项目根目录 config.yaml
./setup.sh --docker
# 自定义：之后的配置、构建、启动保持这个环境变量
export CONFIG_FILE=/opt/everunfold/config/frontend/config.yaml
./setup.sh --docker
./setup.sh --docker deploy
# 不用 Docker：同样设置 CONFIG_FILE，再运行 ./setup.sh --native
```

上例是**宿主路径**。Docker 助手将其所在目录挂到 `/config`，向应用明确传入 `CONFIG_FILE=/config/config.yaml`（自定义文件名会保留）。前端配置目录可写，后端只读；前端产物仍挂 `/workspace`，数据仍挂 `/data`。直接 `docker run` 时，`-e CONFIG_FILE=...` 必须填写**容器内路径**；镜像默认 `/workspace/config.yaml`。若代码或产物目录只读，可把配置放入另一个可写目录后挂载该目录；不要把文件单独 bind mount，否则原子保存会失败。自定义路径里的凭据文件也不得提交 Git。

已有旧向导配置会在首次保存时导入：Docker 使用原 `docker/data/config.toml` 与 `docker/runtime.env`；原生使用 `var/instance/`。没有点击保存就退出，不会写入配置。使用其他位置的旧实例配置时，应先迁入所选实例目录并保留原密钥。

“仅保存”（或 Ctrl+S）成功后保留配置界面并显示成功提示，可以继续修改、再次保存。选择“退出”才离开；未保存的修改会单独询问是否放弃。后端“登记并导出 Base64”同样保留界面，成功后直接显示可复制的 **Base64 编码**，失败可留在界面修正后重试。


选中配置项可点 **“查看 / 复制选中项”** 或按 Ctrl+C；编辑框支持 Shift+←/→ 选择后 Ctrl+C。**F4 / “查看 / 复制结果”** 展示完整执行结果，支持复制全部；导出结果可单独复制 Base64 或导入命令。复制按钮使用终端剪贴板能力（OSC 52）；若 SSH、1Panel 网页终端或本机终端未允许剪贴板，点击 **“终端复制”**，程序临时恢复普通终端，输出完整文字供鼠标选择，按回车返回。Ctrl+Q 或“退出”关闭助手。

后端导出成功后自动展开完整结果，显示节点连接 Base64，以及在**前端仓库根目录**执行的命令，例如：

```bash
./setup.sh --docker --connection-base64
```

执行后按提示粘贴完整编码，输入不回显；也可在前端 setup 的“粘贴后端连接 Base64”或浏览器 `/admin/_nodes` 中导入。原生部署改用 `--native`。命令本身不包含令牌，编码无需共享目录或文件挂载。Base64 不是加密，其中包含节点令牌，请按密钥保管。

## 一、Docker 从零构建与部署

### 1. 准备环境和源码

需要 Linux / WSL、Bash 4+、Git、Docker Engine 和 Compose v2。按 [Docker 官方安装指南](https://docs.docker.com/engine/install/)安装，并确保当前账号可以运行 `docker info` 和 `docker compose version`。宿主无需安装 Python、Node.js 或项目依赖。

准备一个专用 PostgreSQL 空库及有权限访问它的账号，以及可用的 Redis。可以使用现有服务；数据库和 Redis 不包含在两个应用镜像中。如果它们也是容器，在助手的“主网络”或“额外网络”填入这些服务所在的自定义网络，例如 `1panel-network`，再使用容器名访问。默认 `bridge` 不提供容器名解析。数据库由数据库服务自行持久化，后端 `/data` 保存媒体与缓存；运行配置由 `CONFIG_FILE` 指向挂载的 `/config/文件名`。

```bash
git clone https://github.com/wojiaoyishang/Everunfold-Backend.git
cd Everunfold-Backend
docker info
docker compose version
./setup.sh --docker
```

首次运行会自动构建一个轻量配置工具镜像，默认使用 Docker 自带的 `bridge`；自定义主网络不存在时创建，额外网络须已存在。后续会复用工具镜像。工具容器挂载项目目录，使用宿主当前账号的 UID/GID 写配置，不挂载 Docker socket，退出后删除；实际构建由宿主脚本接着执行。

### 2. 在界面中填写必要信息

总览先显示容器设置，可按顺序填写，也可选中单项修改。以你的服务器目录为例：

| 容器设置 | 示例 |
| --- | --- |
| 代码目录 → `/workspace` | `/opt/everunfold/Everunfold-Backend` |
| 数据目录 → `/data` | `/opt/everunfold/Everunfold-Data` |
| 容器名称 | `everunfold-backend` |
| 主网络 | `bridge` |
| 额外网络 | `1panel-network` |
| API 绑定 / API 端口 | `0.0.0.0` / `8001` |
| 宿主映射绑定 IP / 端口 | `0.0.0.0` / `3434` |

后端发布 URL 单独填写，例如 `https://api.example.com`。以上设置对应宿主 `3434 → 容器 8001`；若只允许宿主反向代理访问，宿主映射绑定 IP 填 `127.0.0.1`。也可填宿主具体的 IPv4 或 IPv6 地址。保存后用 `./setup.sh --docker start` 重新创建容器。

助手自动执行“创建容器 → 接入额外网络 → 启动”，不要使用 `--network bridge --network 1panel-network`。配置工具和启动前检查也会接入这些网络。修改网络后，如需在界面检测连接，先“仅保存”并重新打开助手；“构建并启动”会直接用新网络进行启动前检查。所选 Docker 网络 CIDR 可自动允许，额外代理或跨主机来源在手动列表填写。

| 配置                       | 怎么填                                                                  |
| -------------------------- | ----------------------------------------------------------------------- |
| 后端发布 URL               | 对外 API 根地址，例如 `https://api.example.com`，用于媒体等公开资源链接 |
| PostgreSQL URL             | 选中后点击“分项填写”：主机、端口、数据库、用户名和密码                  |
| 运行 Redis URL             | 容器可达的连接地址，例如 `redis://:密码@redis:6379/0`                   |
| Celery Broker              | 留空时沿用 Redis 连接，使用 DB 1                                        |
| 空库自动初始化             | 默认 `1` 开启；设为 `0` 时需手动初始化，仅针对空库                      |
| 密钥、初始管理员密码       | 新站点已生成，可查看和修改                                              |
| API 绑定、worker、宿主端口 | 通常保留默认：容器 `0.0.0.0:8001`，宿主 `127.0.0.1:8001`                |
| HTTPS 代理来源 IP          | 使用宿主反向代理访问 `/_nodes` 时，填写后端实际看到的代理 IP            |

容器中的 `127.0.0.1` 指容器本身，不能代指宿主上的 PostgreSQL / Redis。使用同一 Docker 网络时填写数据库、Redis 的容器名；外部数据库填写其可达地址。特殊字符由 PostgreSQL 分项表单自动编码。

点击“检测连接”，分别查看 PostgreSQL、运行 Redis、Broker 和迁移结果。“连接通过、数据库尚未初始化”是新库的正常状态。检测有超时，失败会显示认证、解析、TLS、连接拒绝等原因；修改配置后旧结果失效。

内部来源默认允许回环；启动时按开关追加所选 Docker 网络的子网，仍须匹配节点令牌；手动列表保留。跨主机部署需填写真实来源或受控代理地址。URL、进程绑定和宿主端口是独立配置。


### 容器互通：推荐共享网络和别名

前后端都加入同一个自定义网络，例如主网络 `bridge`、额外网络 `1panel-network`。两端配置如下：

| 项目 | 后端 | 前端 |
| --- | --- | --- |
| 网络别名 | `everunfold-backend` | `everunfold-frontend` |
| 容器进程绑定 | `0.0.0.0:8001` | `0.0.0.0:3000` |
| 内部调用地址 | 配对接收器填 `http://everunfold-frontend:3000` | 后端内部连接填 `http://everunfold-backend:8001` |
| 允许内部调用的 Docker 网络 | `1panel-network` | `1panel-network` |

容器别名使用**容器内部端口**：宿主映射 `3434 → 8001` 时，这里仍填 `8001`。别名只登记到应用容器的自定义网络，不登记到临时配置或检查容器；默认 `bridge` 没有别名解析。多个前端节点请使用不同别名。

后端配对中的“允许私有网络 HTTP 接收”在 Docker 助手默认开启（已有设置保留），仅允许单段容器别名、RFC1918/ULA 私有 IP 和 `host.docker.internal`；原生默认关闭。通过公网传输继续使用 HTTPS。公开发布 URL、绑定地址和内部连接地址分别配置，浏览器仍使用 HTTPS 发布地址。

在两端“容器设置 → 允许内部调用的 Docker 网络”填写 `1panel-network`，也可用逗号列出多个已连接的网络。后端以这些子网核对前端请求的真实来源，前端接收器以这些子网核对后端同步请求的真实来源。指定网络优先于旧的自动允许开关，手填 IP/CIDR 额外生效，内部接口继续验证节点令牌。留空沿用旧开关：`1` 追加全部已连接网络的范围，`0` 只用手填来源。

如果宿主机 Nginx 经该网桥访问容器，设置“容器设置 → 宿主机反代所在的 Docker 网络”为 `1panel-network`。向导启动时将该网桥的网关 IP 加入节点控制台的可信代理列表，宿主反代须保留公开 Host 并发送 `X-Forwarded-Proto: https`。按启动日志的网关与拒绝日志的 `peer_ip`（前端日志为 `peer`）核对来源。若代理在另一个容器或其他主机，按实际来源填写“HTTPS 反向代理的直接来源 IP”。

网络名称保存在根配置的 `docker` 小节。重建网络后运行 `./setup.sh --docker start`，助手会读取新的 IP 和子网。

另一种方式是在两端开启 **“映射 host.docker.internal”**（默认 `0`）。脚本添加 `--add-host host.docker.internal:host-gateway`，配置工具、启动前检查和运行容器使用相同映射。这是主机名映射，无需新增文件挂载。前端内部地址此时填 `http://host.docker.internal:3434`，使用**宿主映射端口**。后端接收 URL 同理填写前端的宿主映射端口。宿主端口若只绑定 `127.0.0.1`，容器不能通过网关访问，应使用可达的宿主绑定地址，或改用推荐的共享网络别名。

沿用全部网络自动允许且开启宿主网关时，还会允许默认 bridge 的网关 IP，以兼容宿主端口转发来源。使用按名称指定的网络时，额外宿主调用来源可手填。原生模式不自动加入 Docker 网络来源。网络及网关配置修改后，在助手中检测前先保存并重新打开；构建/启动操作会直接使用新设置。

依据：[Docker 自定义网络 DNS](https://docs.docker.com/engine/network/)、[网络别名](https://docs.docker.com/reference/cli/docker/network/connect/)、[host-gateway](https://docs.docker.com/reference/cli/dockerd/#configure-host-gateway-ip)。

### 3. 构建、初始化与启动

总览另提供 **“初始化数据库并构建启动”**：一次保存配置，构建运行镜像，再显式初始化空库、复检并启动容器。即使“空库自动初始化”为 `0` 也可执行，且不会改写该开关。已有库已同步时跳过初始化；不会清库或重设账号，旧版本仍需先升级迁移。

```bash
./setup.sh --docker init-deploy
```

构建只执行一次。构建或初始化失败时不创建新服务容器，也不停止已有容器。各阶段日志实时显示。

新站点保留“空库自动初始化”为 **`1`**，直接选择 **“构建并启动”**。助手先检查 PostgreSQL、Redis、Broker；确认数据库无用户表、视图、序列等关系对象后，自动执行核心迁移、创建初始管理员、登记插件，复检通过再创建并启动服务容器。每个阶段都输出日志，任何阶段失败都会停止。

已经保存配置时，等价命令为：

```bash
./setup.sh --docker deploy
curl --fail http://127.0.0.1:8001/health/ready
docker logs --tail=100 everunfold-backend
```

“初始化新库”按钮或 `./setup.sh --docker init-db` 只做初始化，成功后会明确提示执行 `start`。保存过配置也可以使用该按钮；是否需要初始化由数据库实际状态决定，不再用“已有站点”选项阻止。已就绪的数据库直接跳过，不重设管理员密码。

根 `config.yaml` 的 `environment.EVERUNFOLD_AUTO_INIT_EMPTY_DB` 保存此开关，值为字符串 `"1"` / `"0"`，旧配置缺少此字段时默认开启。关闭后 `start` 遇空库会停止并说明原因；手动 `init-db` 仍可初始化。`check` 和界面的“检测连接”始终只读。已有对象但缺迁移记录的库不会被当成空库；旧版本库仍要求显式 `migrate`。并发初始化由数据库会话锁阻止。

默认初始管理员用户名是 `admin`，密码可在配置助手的“初始管理员密码”中查看。首次保存后再次打开会进入已有站点模式；初始密码也保存在私有根配置的 `environment` 中，修改该字段不会重设已创建账号。

已有数据库使用“保存并构建”或下列命令。备份数据库和媒体后，在维护窗口显式升级；迁移操作会停止所选后端容器，成功后需要重新启动：

```bash
./setup.sh --docker build
./setup.sh --docker migrate
./setup.sh --docker start
```

数据库迁移已经同步时，可直接选择“构建并启动”。“仅保存”不构建、不启动；构建失败保留已保存配置，可修复原因后重跑 `build`。所有构建日志实时回显。

### 4. 配置 HTTPS 入口并导出前端连接

准备 API 的 HTTPS 反向代理，将 `https://api.example.com` 转发到宿主 `127.0.0.1:8001`。代理保留公开 Host 并覆盖 `X-Forwarded-Proto: https`。在助手中选择宿主机反代所在的 Docker 网络，或填写后端实际看到的代理 IP。

节点控制台在**后端发布 URL** 的 `/_nodes`，例如 `https://api.example.com/_nodes`，与容器绑定地址无关。发布 URL 优先于旧配置残留的 `nodes_monitor.origin`，不会再被旧 `127.0.0.1:8001` 覆盖。助手保存时清除这个重复地址。

setup 的“节点控制台”分组提供**启用开关（1/0）、密码和会话密钥**。新配置自动生成随机密码，没有固定默认密码；选中即可查看、复制、修改或重新生成。已有启用状态保留，只有密码哈希的旧实例留空保留密码；修改密码或会话密钥使旧登录失效。密码与管理员密码独立。向导“HTTPS 反向代理的直接来源 IP”可添加其他确需信任的反代对端。

在助手中填写待配对的“前端节点 ID”和“此节点的内部接收器 URL”，例如 `web-main`、`https://receiver.example.com`，选择“登记并导出 Base64”，或者保存后运行：

```bash
./setup.sh --docker export-node
```

终端直接输出节点连接 Base64，只包含该节点 ID、后端发布 URL、来源 UUID 和双向令牌，不包含数据库、Redis 或站点主密钥。前端在 setup 或 `/admin/_nodes` 粘贴导入，无需跨容器访问文件。不同前端分别选择不同节点 ID 导出，原节点令牌保留。新增令牌后执行 `./setup.sh --docker start`，让运行中的后端重新加载环境。

保存后重启后端，`/_nodes` 会显示已经保存的配对，例如 `web-main`。点击“配置并登记”，核对内部接收器、私网 HTTP 和令牌状态并按需启用。节点 ID、双向令牌和来源规则沿用 setup；无需填写前端公开地址。前端只在自己的 setup 配置发布 URL，运行后自动认证上报给后端。已有节点通过“配置”调整内部接收器和启停，更换接收器前先停用。在线修改的接收器保存在数据库，重复导出相同凭据时保留。令牌与来源规则仍在 setup 修改并重启进程。应用后台“设置 → 节点状态与维护”负责首次公开内容同步。

`/_nodes` 的「同步来访记录」可查看最近 15 分钟、1 小时、6 小时或 24 小时的来源 IP、节点、令牌校验、HTTP 状态和失败原因。被白名单或节点状态挡住的请求显示「未校验」。同步请求受理后，实际投递结果在节点同步状态中查看。记录只在启用控制台时收集，使用 Redis，最多保留 24 小时及最近 4000 条；不包含令牌原文。

### 5. 文件、挂载与日常操作

助手自动生成并维护这些文件，无需逐个复制模板：

```text
config.yaml                 项目根目录的统一部署配置
config.runtime.toml         自动生成的兼容配置，勿手改
docker/
  .env                     自动生成的 Compose 参数
  runtime.env              自动生成的兼容进程环境
  data/
    setup.json             当前待配对节点的信息
    uploads/               持久化媒体
```

后端代码目录只读挂到 `/workspace`，所选数据目录可写挂到 `/data`。所选配置目录另挂 `/config`，使用显式 `CONFIG_FILE`，API、Worker、Scheduler 启动前统一加载其中的环境值；不需要 `/data/config.toml`，也无需手工传 `--env-file`。新配置将容器 UID/GID 与执行助手的宿主账号对齐；已有 `EVERUNFOLD_UID/GID` 保留。重新构建镜像、删除应用容器都不会删除宿主数据。

镜像默认 `everunfold-backend:python312`，包含 Everunfold 项目标签、构建来源和时间；构建阶段及启动日志也显示组件名。镜像中的依赖按 `uv.lock` 安装，源码仍从挂载读取。依赖锁变化后必须重建镜像。

```bash
./setup.sh --docker                 # 重新编辑配置
./setup.sh --docker check           # 用实际运行镜像检查配置、数据库、Redis、迁移
./setup.sh --docker init-deploy     # 构建、显式初始化空库并启动
./setup.sh --docker build           # 只构建
./setup.sh --docker start           # 检查后启动或重新创建
./setup.sh --docker sync            # 重新投递当前节点的同步快照
docker restart everunfold-backend
docker stop everunfold-backend
```

挂载点在向导中编辑，保存到根 YAML 的 `docker` 小节；相对路径以 `docker/` 为基准。选择另一个代码目录也只读取 `CONFIG_FILE` 指定的原文件，不复制第二份配置。更换数据目录只改变挂载，不搬运原有媒体；请选择原数据目录或自行迁移。容器名称相同时会重新创建属于当前部署的容器，保留宿主数据；遇到无关的同名容器会停止并提示更名。上方日志、停止和重启命令需使用你选定的容器名称。启动统一使用根 `setup.sh`，Compose 用于构建，不要用 `compose up` 绕过额外网络接入。导入旧配置后，根 YAML 中其他应用字段和环境变量会保留，保存前创建 `*.bak` 备份；原 TOML 注释保留在备份里。备份时一并保存根目录 `config.yaml`、数据和数据库。

### 手动触发同步

修正连接问题后，在 setup 选择“触发同步”，或执行 `./setup.sh --docker sync`（原生用 `./setup.sh --native sync`）。后端使用部署数据库权限应用当前代码的后端插件清单，并将所选前端节点的公开设置、缓存策略和内容重新排队，已耗尽的重试次数会重置。节点须在 `/_nodes` 已启用且绑定内部接收器。排队成功后，通过节点状态和部署日志确认 Worker 投递结果。

Docker 模式在本项目运行中的后端容器执行。原生模式由部署账号运行项目工具。多节点部署可由部署账号在运行环境中执行 `python -m tools.trigger_frontend_sync --node-id web-main` 显式选定节点。

前端部署助手的同步请求使用前端到后端的节点令牌，且真实来源 IP 必须在后端允许列表内。该请求只能将自身节点的同步重新排队。

### 已有部署升级到根配置

保留已有根 `config.yaml` 和媒体目录，更新代码后运行 `./setup.sh --docker`，确认挂载、端口和网络，再选“构建并启动”。本次后端镜像新增 YAML 读取依赖，**需要重建镜像**；仅重启旧镜像无法应用新入口。如果之前只有旧 TOML / env，请先让助手导入原配置，不要重新生成已有站点的主密钥。旧 `/data/config.toml` 不再是运行依赖，也不会被自动删除。

## 二、不使用 Docker

### 1. 准备依赖和数据库

需要 Linux / WSL、Bash、Git、Python 3.12、[uv](https://docs.astral.sh/uv/getting-started/installation/)、PostgreSQL 和 Redis。uv 会按工具锁文件准备配置界面的依赖；业务依赖仍使用仓库根目录的 `uv.lock`。

准备专用空库和数据库账号。数据库、Redis 与后端都在本机时，可以使用 `127.0.0.1`；如果运行在其他服务器上，填写其实际地址。

```bash
git clone https://github.com/wojiaoyishang/Everunfold-Backend.git
cd Everunfold-Backend
./setup.sh --native
```

这个入口完全不调用 Docker。仍使用同一个彩色助手，Docker 专属字段显示为“不使用”。只需填写数据库、Redis 和后端发布 URL；本机测试可保留 `http://127.0.0.1:8001`。新站点密钥已生成，点击检测核对连接。

### 2. 初始化并运行

选择 **“初始化数据库并构建启动”**，或保存配置后执行 `./setup.sh --native init-deploy`，可一次安装锁定依赖、显式初始化空库并启动本端进程；同样保留已有数据和自动初始化开关。

新站点保留“空库自动初始化”为 `1`，选择“构建并启动”，或者仅保存后运行：

```bash
./setup.sh --native build
./setup.sh --native start
```

`build` 使用 `uv sync --locked --no-dev` 准备后端依赖。`start` 检查依赖，按开关初始化空库，复检后在前台分别运行 API、Worker、Scheduler；Ctrl+C 统一停止。原生模式与 Docker 使用同一开关。已有库升级仍显式执行；单独托管的 `api`、`worker`、`scheduler` 入口不执行初始化。

另开终端检查：

```bash
curl --fail http://127.0.0.1:8001/health/ready
./setup.sh --native check
```

已有站点选择“已有站点”，填写并保留原主密钥；迁移前备份并停止旧进程，再执行 `./setup.sh --native migrate`。程序不会重设已有管理员密码。

### 3. 配对前端和长期运行

回到助手填写节点 ID 和接收器地址。两端都在本机直接运行时，接收器可以填写 `http://127.0.0.1:3000`。选择“登记并导出 Base64”，界面内显示成功和完整 Base64；也可运行：

```bash
./setup.sh --native export-node
```

前端运行 `./setup.sh --native --connection-base64`，按提示粘贴编码。新增节点令牌后，重启运行中的后端进程以载入新环境。后台节点公开 URL 可以是本机 `http://127.0.0.1:3000`；从其他机器访问时，应改为实际 HTTPS 地址并配置反向代理。

原生部署同样直接读取根 `config.yaml`；派生兼容 TOML 位于根 `config.runtime.toml`。数据默认使用 `var/instance/`：`runtime.env`、`deploy.env`、`setup.json`、`uploads/` 均在这里，与 Docker 数据分开。程序会自动加载根配置的环境值，无需手工 `source` 或逐条 `export`。用 `EVERUNFOLD_INSTANCE_DIR=/绝对路径` 可以选择其他实例目录，每次启动保持一致。

长期托管可让 systemd / Supervisor 分别运行以下命令，使用同一个服务账号和实例目录；每个实例只启动一个 Scheduler：

```bash
./setup.sh --native api
./setup.sh --native worker
./setup.sh --native scheduler
```

API worker 数量、Celery 池及并发数从 YAML 的 `environment` 读取，外部环境变量可覆盖。配置更新后重新启动对应进程。备份根目录 `config.yaml`、`var/instance` 和数据库。

### 旧开发联调入口

原 Supervisor / Nginx 开发模板继续保留，命令与端口约定见[开发容器部署指南](../docs/development/container-deployment.md)。该模板与本文的根目录部署助手分开使用。

### 控制台访问与拒绝原因

“构建并启动”会保存配置、重建镜像并重新创建应用容器。控制台仍无法访问时，查看启动日志中的实际 `CONFIG_FILE` 和 `[Everunfold access]`：

- `monitor_disabled`：该实例未启用控制台，路由返回 404。
- `monitor_host_mismatch`：请求 Host 与发布 URL 不符；`monitor_https_required`：缺少正确的 HTTPS 协议。
- `monitor_proxy_untrusted`：实际直连 TLS 代理 IP 未获信任。核对“宿主机反代所在的 Docker 网络”推导的网关，或填写日志中的实际代理 IP。
- `peer_not_allowed`、`token_missing`、`token_mismatch`：内部上报的真实来源或令牌未通过。日志含来源 IP、节点 ID及有效允许列表，不含密码、令牌、正文或查询参数。
- `node_not_registered` / `node_disabled`：节点未加入运行清单或未启用。在后端 `/_nodes` 选择相应的配对，登记并启用；令牌也须匹配，不需要补填前端公开地址。

内部调用使用所选网络的实际 CIDR 与手填来源，保留 IP **且** Token 校验。HTTPS 节点控制台用所选宿主网桥的网关或手填代理 IP 校验直连代理。

setup 的“日志”分组提供 `API_LOG_LEVEL`、`WORKER_LOG_LEVEL`、`SCHEDULER_LOG_LEVEL`，支持 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。新配置默认分别为 `INFO`、`WARNING`、`INFO`；已有值保留，原 Worker 为 `INFO` 时请手动改为 `WARNING`。保存并启动后生效。Scheduler 只记录状态转变，不逐次输出心跳；相同访问拒绝日志节流 30 秒。降低 API 日志等级会相应隐藏诊断信息。

在“进程”分组中，使用 ↑/↓ 和 Enter 选择 Celery 执行池。选择「多进程（prefork）」后，可以选择「固定并发」或「按任务量自动伸缩」。自动模式默认最小 1、最大 4；由 Celery 在任务积压时扩容，任务完成并空闲一段时间后缩回，执行中的任务不会因为缩容被中断。单进程（solo）始终逐个执行任务，已有配置继续使用固定并发。

配置保存在根 YAML 的 `environment`。自动模式对应 `WORKER_POOL: "prefork"` 和 `WORKER_AUTOSCALE: "4,1"`（最大在前）。固定模式把 `WORKER_AUTOSCALE` 留空，并使用 `WORKER_CONCURRENCY`。Docker 首次启用此功能要更新后端源码并选择「构建并启动」以更新入口脚本；此后调整范围只需保存并重新创建容器。原生方式保存后重新启动。
