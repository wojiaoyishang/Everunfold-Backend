# 前端节点监控

FastAPI 直接提供 `/_nodes`。页面按稳定节点 ID 显示前端上报、接收器和同步状态。登录、页面、样式及脚本都由 `app/plugins/nodes_monitor/` 提供。

## 配置

1. 从 `frontend-nodes.example.toml` 创建独立节点清单，移入原来的 `[frontends.<节点 ID>]`。主 `config.toml` 根级填写 `frontend_nodes_file = "./frontend-nodes.toml"`，相对路径以主配置所在目录为准。保留节点 ID 和 Token 环境变量引用。旧版内联节点配置仍可单独使用。
2. 运行 `uv run --locked python -m tools.nodes_monitor_password`，将输出的 `password_hash` 放入主配置的 `[nodes_monitor]`。设置 `enabled = true`。独立签名密钥通过 `session_secret_env` 指定的环境变量注入，默认名称为 `EVERUNFOLD_NODES_SESSION_SECRET`。用 `python -c 'import secrets; print(secrets.token_urlsafe(32))'` 生成随机值，所有 API 副本使用同一个值。
3. `origin` 留空时取 `BACKEND_BASE_URL`。生产使用 HTTPS。若终止 TLS 的代理与后端分离，限制后端端口只接受受信代理的流量，并配置 ASGI 仅从受信代理识别 HTTPS；不要信任任意转发头。当前容器 HTTP 配置仅接受回环，入口为 `http://127.0.0.1:3000/_nodes`。现有独立接收器端口和 `/internal/*` 继续对外关闭。
4. 修改节点清单后同步重启 API、Worker 和 Scheduler。前端接收器升级后也需重启以启用只读探测。Nginx 代理使用仓库模板的 `/_nodes` 规则。

开发实例初始密码在后端忽略目录 `var/container/nodes-monitor-credentials.json`，权限为 0600。会话默认 30 分钟。更换密码哈希或签名密钥并重启 API 后，原有会话失效。退出会清除当前浏览器的节点会话。登录限流在每个 API 进程独立维护，多个 API 副本可在代理层按来源进一步限流。

## 读懂状态

- 「前端上报」读取现有扩展清单的最后接收时间。正常运行约每分钟上报一次，超过 150 秒显示上报超时。
- 「最后确认」来自后端已核对的投递回执。「接收器副本」来自这次独立探测。同步接收器连接正常时仍可能存在缺失副本、版本落后或已经耗尽重试。
- 页面每 15 秒更新，也可手动刷新。数据库或 Redis 故障时，独立登录继续可用，相关状态显示无法读取。接收器地址、密钥和任务内容由服务端保管。
- 节点文件用于调整地址和启用状态，原后台的重新同步入口负责重发。
