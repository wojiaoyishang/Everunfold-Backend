# 契约发布目录

后端是API/WS/事件契约的唯一权威发布者，前端从确定提交生成类型/客户端并记录SHA；文件名中的早期版本不等于当前版本，以`x-everunfold-contract-version`为准。

| 产物 | 当前版本 | 范围 |
| --- | --- | --- |
| auth-v0.1.schema.json | 0.2.0 | Cookie Session、CSRF、当前身份/授权、强制改密状态 |
| accounts-v1.schema.json | 1.4.0 | 私有资料、会话/密码、用户/授权、身份预设、Key/应用与受限头像库 |
| api-key-protocol-v1.schema.json | 1.0.0 | 应用签名校验、最小响应、签名回调/ACK与缓存边界 |
| content-admin-v1.schema.json | 1.10.0 | 草稿、修订/分支/合并、语言关系与拥有者字段 |
| collaboration-v1.schema.json | 2.1.0 | 持久协同、源码租约、光标/合并暂停及提交回执拥有者字段 |
| content-access-v1.schema.json | 1.1.0 | 私有受限正文读取 |
| settings-v1.schema.json | 1.10.0 | 站点/主题/系统、可扩展语言目录 |
| public-v0.1.schema.json | 0.7.0 | 可公开的完整快照、多语言与已应用页面 |
| pages-v1.schema.json | 1.1.0 | 页面草稿/应用、可信清单、独立私有元数据接口 |
| comment-likes-v0.1.schema.json | 0.1.0 | 匿名历史评论点赞 |

权威导出入口为`tools/export_*_contract.py`；例如`uv run --locked python -m tools.export_accounts_contract`。协议Schema与独立Node参考对应，见[接入指南](../docs/development/api-key-applications.md)。`presentation-v0.2.schema.json`和`reader-preview-v0.1.schema.json`仍分别标记preview，不把展示草案当作正式写接口。`public-openapi-v0.1.json`由现有FastAPI公开业务路由导出，完整应用另含诊断路由。

身份及写请求遵守真实HTTP状态和`success/code/msg/data`外壳；`msg`仅供本地化，客户端按稳定code处理。业务路径`/v1/*`，内部`/internal/v1/*`，前端接收`/internal/cache/v1/*`，协同`/ws/v1/collab/{draft_id}`；开发代理`/api`不进入业务契约。

资料、Key及账号/应用管理仅Session+精确Origin/CSRF；Bearer用于已明确登记能力的业务API，失败不借用Cookie。私有资料/凭据不进公开快照，接口、迁移与部署顺序见[账号指南](../docs/development/accounts.md)。
