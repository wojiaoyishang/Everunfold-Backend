# 契约发布目录

基础登录使用 `auth-v0.1.schema.json`（0.1.0），由 `uv run --locked python -m tools.export_auth_contract` 导出。`/v1/auth/session` 返回匿名/已登录身份及会话绑定 CSRF token，登录/退出使用 `X-CSRF-Token` 与精确 Origin；所有请求带凭据且响应 no-store。`/v1/admin/session` 另验证管理员权限。操作与错误码在契约扩展字段中，实施及验收记录随本轮后端提交更新。

评论点赞使用 `comment-likes-v0.1.schema.json`（0.1.0），由 `uv run python -m tools.export_comment_likes_contract` 导出；GET 批量读取/PUT 明确设置，匿名访客头和操作元数据随契约发布。需要显式应用 `0002_comment_likes`，不依赖演示开关。仅覆盖真实公开历史评论，详见 [ADR 0015](../docs/decisions/0015-reader-feedback-and-comment-likes.md)。

前台原型另消费 `presentation-v0.2.schema.json`（`0.2.0-preview.1` 草案），由 `app/modules/content/presentation_schema.py` 维护，使用 `uv run --locked python -m tools.export_presentation_contract` 导出。它统一 article/shuoshuo/page 的顶层 type，预留评论开放、公开互动计数与访问状态；不包含浏览者点赞/身份，也不允许受限记录携带正文、摘要或媒体。它不是新的在线 API、数据库结构或已上线快照版本；当前 0.1.0 仍保持不变，前端使用适配层和明确标注的样例。后续正式升级须另外验收迁移与缓存兼容。

后端是公开 API、WS 和内部事件契约的唯一权威发布者，前端从确定版本生成 TypeScript 类型与客户端并锁定版本。

当前公开阅读契约为 `0.1.0`：

- `public-v0.1.schema.json`：完整公开快照的 JSON Schema（draft-07）。后端导出后由前端验证并读取；前端生成 TypeScript 类型，记录确定来源提交和 SHA256。
- `public-openapi-v0.1.json`：由当前 FastAPI 公开业务路由导出的 OpenAPI，只包含 `/v1/*`；应用 `/openapi.json` 另包含诊断路由。重导出命令：`uv run --locked python -m tools.export_contracts`。

这不是完整 WS/ICP/写业务契约。当前只有公开只读 API；正式协同、认证、可靠缓存回调和写操作须单独定义和验收。内部快照不通过未受保护的 HTTP 写接口接收；当前开发环境显式导出文件，见开发文档。

路径边界：公开 `/v1/*`，后端内部 `/internal/v1/*`，前端接收 `/internal/cache/v1/*`，协同 `/ws/v1/collab/{draft_id}`。具体端点与消息 Schema 按基线继续设计。开发 `/api` 不进入后端 OpenAPI。
