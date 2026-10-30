# 契约发布目录

后端是公开 API、WS 和内部事件契约的唯一权威发布者，前端从确定版本生成 TypeScript 类型与客户端并锁定版本。

当前尚无业务契约发布。`/openapi.json` 只反映初始化应用的活性检查，不把它当作 `/v1/*` 已定稿契约，也不先手写虚构的客户端。后续在本目录按版本维护 OpenAPI、WS/ICP JSON Schema、错误码与兼容性说明；变更同时更新两仓库交接记录。

路径边界：公开 `/v1/*`，后端内部 `/internal/v1/*`，前端接收 `/internal/cache/v1/*`，协同 `/ws/v1/collab/{draft_id}`。具体端点与消息 Schema 按基线继续设计。开发 `/api` 不进入后端 OpenAPI。
