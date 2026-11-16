# 前端节点监控

FastAPI 直接提供 `/_nodes`。页面按稳定节点 ID 显示前端上报、接收器和同步状态。登录、页面、样式及脚本都由 `app/plugins/nodes_monitor/` 提供。

## 配置

推荐使用根 `./setup.sh`。`CONFIG_FILE` 默认项目根 `config.yaml`；下列 TOML 示例仅供显式指定旧配置的实例参考。TLS 代理、拒绝日志和日志等级见[部署教程](../../deploy/README.md#控制台访问与拒绝原因)。

1. 从 `frontend-nodes.example.toml` 创建独立节点清单，移入原来的 `[frontends.<节点 ID>]`。主 `config.toml` 根级填写 `frontend_nodes_file = "./frontend-nodes.toml"`，相对路径以主配置所在目录为准。保留节点 ID 和 Token 环境变量引用。旧版内联节点配置仍可单独使用。
2. 运行 `uv run --locked python -m tools.nodes_monitor_password`，将输出的 `password_hash` 放入主配置的 `[nodes_monitor]`。设置 `enabled = true`。独立签名密钥通过 `session_secret_env` 指定的环境变量注入，默认名称为 `EVERUNFOLD_NODES_SESSION_SECRET`。用 `python -c 'import secrets; print(secrets.token_urlsafe(32))'` 生成随机值，所有 API 副本使用同一个值。
3. `origin` 留空时取 `BACKEND_BASE_URL`。生产使用 HTTPS。若终止 TLS 的代理与后端分离，限制后端端口只接受受信代理的流量，并配置 ASGI 仅从受信代理识别 HTTPS；不要信任任意转发头。当前容器 HTTP 配置仅接受回环，入口为 `http://127.0.0.1:3000/_nodes`。现有独立接收器端口和 `/internal/*` 继续对外关闭。
4. 执行 `uv run --locked alembic -c migrations/alembic.ini upgrade head`，再升级 API、Worker、Scheduler 和前端接收器。Nginx 代理使用仓库模板的 `/_nodes` 规则。

## 在后端页面管理清单

使用现代 setup 时，保存配对并重启后端。登录 `/_nodes` 后，未登记的配对显示“设置公开地址”。填入真实的前端发布 URL，选择是否启用再保存，即可沿用后端 setup 的内部地址、私有 HTTP 设置和令牌引用。以下命令同时适用于显式管理旧部署清单。

运行 `uv run --locked python -m tools.import_frontend_nodes`，将部署文件中的清单一次性导入 PostgreSQL。导入前仍可手动维护 TOML，改动后重启后端进程。导入后，后端进程从数据库读取同一份运行清单，页面里的新增、修改、停用和删除会即时生效。

1. 在 `/_nodes` 新增节点 ID 和公开站点地址。尚未登记连接时先保存为停用。
2. 在后端的 `frontend-nodes.toml` 写入该节点的接收器地址和两个环境变量引用，并分别为 API 和 Worker 注入所需 Token。运行 `uv run --locked python -m tools.import_frontend_nodes --update-bindings`。更新部署连接时保留页面上的公开地址、启用状态和节点成员关系。已启用的节点须先停用才能更换绑定。
3. 在页面启用节点。配置了接收器的节点会收到当前站点设置、缓存策略和公开内容。使用「重新同步」可单独为该节点安排重发。显示「已确认」表示接收器已返回有效回执。
4. 删除后，后端拒绝该节点后续上报并停止安排和派发投递。已经在网络上传输的请求可能到达，旧租约无法再确认。节点历史与本机公开副本保留。退役设备还须撤销它的部署 Token、关停接收器与公开入口。不要把同一个 ID 交给不相关的设备。

多设备部署时，从前端验证 FastAPI 地址，从实际发起投递的 Worker 验证前端接收器地址。内部服务使用 HTTPS 和真实连接 IP 白名单，两个方向分别配置 Token。接收器及上报接口通过私网或受限入口部署。

前端的 `/admin/_nodes` 使用前端本机的连接文件。在线时该页通过站点管理员会话读取全站状态；本机密码可在后端不可用时新增或编辑连接。前端的诊断密码和后端 `/_nodes` 密码分别配置。

## 更换密码

开发实例的初始密码在本地 `var/container/nodes-monitor-credentials.json` 的 `password` 字段，权限为 0600。要换成自选密码，在后端目录运行 `.venv/bin/python -m tools.nodes_monitor_password`，按提示输入两次新密码。将输出的整行 `password_hash = "…"` 替换进 `config.toml` 的 `[nodes_monitor]`，执行 `./deploy/container-dev.sh restart api`。更换时无需知道旧密码。用新密码登录成功后，可以删除保存旧密码的本地凭据文件。

会话默认 30 分钟。更换密码哈希或签名密钥并重启 API 后，原有会话失效。退出会清除当前浏览器的节点会话。登录限流在每个 API 进程独立维护，多个 API 副本可在代理层按来源进一步限流。

## 读懂状态

- 「前端上报」读取现有扩展清单的最后接收时间。正常运行约每分钟上报一次，超过 150 秒显示上报超时。
- 「最后确认」来自后端已核对的投递回执。「接收器副本」来自这次独立探测。同步接收器连接正常时仍可能存在缺失副本、版本落后或已经耗尽重试。
- 页面每 15 秒更新，也可手动刷新。数据库或 Redis 故障时，独立登录继续可用，相关状态显示无法读取。接收器地址、密钥和任务内容由服务端保管。
- 管理清单不可用时，页面显示部署清单中的参考节点，并停止探测这些可能过时的地址。

节点配对导出默认返回标准 Base64（UTF-8 JSON，`everunfold-node-connection/v1`），不生成跨容器连接文件。它只包含当前节点的发布 URL、来源 UUID 和双向令牌。前端 setup 与浏览器支持粘贴导入；从 `/_nodes` 中选用已保存的配对，设置公开地址和启停。
