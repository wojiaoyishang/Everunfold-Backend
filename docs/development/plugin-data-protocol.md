# 插件数据拉取与回调接口规范

版本：plugin-data-1，2026-11-07。状态：先冻结接口与边界，再实现适配；实际可用操作与验收以STATUS为准，未列入实现清单的后端资源不得返回假成功。

## 插件声明和调用

每个资源使用稳定resource_id（例如core.people.settings），声明scope为public或session、参数/结果模型、真实后端operation以及可选通知主题。资源必须来自已安装后端能力；前端插件声明不会自动创建数据库表、HTTP接口或新权限。服务器地址与内部凭据仅由宿主配置，插件参数不接受任意callback_url或代理URL。

宿主提供data.refresh<T>(resource_id, params, {signal, knownRevision?})立即发起真实后端请求；需要等待异步结果时提供data.request<T>(resource_id, params, {mode:"deferred", operationId, signal})，返回request_id、accepted_revision及状态pending。data.wait<T>(request_id, {afterRevision, timeoutMs, signal})等待变更，data.subscribe(resource_id, params, listener)只订阅已授权资源。没有配置真实operation的调用明确返回unavailable，不回退未验证的静态成功数据。

返回统一ResourceResult<T>：resource_id、scope、source_epoch、revision、updated_at、data；待处理返回request_id/state/pending，不与数据保存或节点收到混为一谈。revision在同一资源/参数摘要/epoch内单调，跨epoch不能比较；旧回调不能覆盖新状态。取消等待只取消订阅，不撤销已提交后端任务。超时保留已知值并提示可重试。

## 立即拉取

浏览器业务请求沿已有生成客户端直接访问FastAPI，Session调用保留凭据、精确Origin、CSRF及后端权限。公开资源可进入公开副本，session结果仅留该用户请求/组件生命周期内存，不写公共磁盘、SSR共享缓存或localStorage。失败与未修改使用真实HTTP/业务状态，不能凭msg判断；幂等写操作必须携带稳定operationId并由后端确认。

## 延迟结果和后端Callback

请求接收只表示排队成功。业务与Outbox同一PostgreSQL事务提交；Worker执行，Scheduler只调度。后端Callback目标为受信前端节点的内部接收器，复用真实socket来源IP AND Token、节点身份和既有可靠重试/去重；不向访客提供内部Token，也不直接调用浏览器随机URL。

回调信封包含protocol、event_id、node_id、resource_id、scope、request_id、params_digest、source_epoch、revision、state（ready/failed/invalidated）、完整结果或失效通知、payload_digest。公开ready只在前端持久化完成后ACK；应用按resource_id/params_digest隔离并拒绝乱序、错误摘要及已撤回资源。返回ACK包含event_id、applied_revision及status（applied/already_applied/stale/rejected）；失败可可靠重试，不得提前标记完成。

私有回调不落公共缓存；只通知request_id/资源版本，浏览器随后携带自己的Session拉取并重新鉴权。订阅断开/重新登录/权限撤销清理私有监听。SSE/WS通知只是提示通道，丢事件后以授权refresh恢复；业务事实不依赖浏览器常在线。不同用户或角色不能通过订阅topic、请求ID或错误信息探测受限结果。

## 当前已有能力和接入顺序

公开页面配置目前已经通过页面应用事务、完整public-content快照、Worker与Node接收器可靠下发，不新增一条绕过版本/撤回的回调。管理编辑需要最新工作稿时调用readManagedPage/readPageMetadata；草稿与元数据不会被公开Callback带出。后续第一个真实异步插件资源接入上述deferred/request/wait流程时，必须同时发布后端契约并验证双API、重复/乱序回调、离线恢复、权限撤销与超时，不先上线未鉴权的万能数据代理。
