# 契约发布目录

后端是API/WS/事件契约的唯一权威发布者，前端从确定提交生成类型/客户端并记录SHA；文件名中的早期版本不等于当前版本，以`x-everunfold-contract-version`为准。

| 产物 | 当前版本 | 范围 |
| --- | --- | --- |
| auth-v0.1.schema.json | 0.3.0 | Cookie Session、CSRF、当前身份/授权、强制改密状态 |
| accounts-v1.schema.json | 1.5.0 | 私有资料、会话/密码、用户/授权、身份预设、Key/应用与受限头像库 |
| api-key-protocol-v1.schema.json | 1.0.0 | 应用签名校验、最小响应、签名回调/ACK与缓存边界 |
| captcha-v1.schema.json | 1.12.0 | 验证码、阅读限制、讨论准入、评论发布回执与受保护页面标题显示 |
| collaboration-v1.schema.json | 2.12.0 | 持久协同、源码租约、光标、合并暂停和草稿设置（含空摘要开关） |
| comment-likes-v0.1.schema.json | 0.2.0 | 匿名历史评论点赞 |
| content-access-v1.schema.json | 1.11.0 | 私有受限正文读取与读者投影 |
| content-admin-v1.schema.json | 1.27.0 | 内容草稿、修订和公开投影，含文章卡片摘要设置 |
| glossary-v1.schema.json | 3.7.0 | 术语索引/详情、特色图片/图片查看开关、按内容匹配与跨页面 DOM/脚本触发 |
| maintenance-v1.schema.json | 1.0.0 | 原生数据包、导入预检与导入记录、健康检查 |
| notifications-v1.schema.json | 1.4.0 | 通知模板、邮件品牌、模板变量、投递记录与当前账号 Inbox |
| operations-v1.schema.json | 1.2.0 | 评论查询、审核与评论格式 |
| pages-v1.schema.json | 1.16.0 | 页面与模板版本历史、恢复草稿、模板应用影响确认、隐藏页面标题 |
| presentation-v0.2.schema.json | 0.2.0-preview.10 | 内容展示（草案），含文章卡片摘要设置 |
| public-openapi-v0.1.json | 0.1.4 | 当前 HTTP 业务路由和统一错误响应；仅离线导出 |
| public-v0.1.schema.json | 0.21.0 | 公开快照，含文章卡片摘要设置 |
| reader-preview-v0.1.schema.json | 0.1.0-preview.1 | 阅读演示指标（预览） |
| settings-v1.schema.json | 1.51.0 | 站点、主题、系统、节点与讨论设置，含页脚 HTML、默认字体缩放及媒体查询置顶 |

权威导出入口为`tools/export_*_contract.py`；例如`uv run --locked python -m tools.export_accounts_contract`。协议Schema与独立Node参考对应，见[接入指南](../docs/development/api-key-applications.md)。`presentation-v0.2.schema.json`和`reader-preview-v0.1.schema.json`仍分别标记preview，不把展示草案当作正式写接口。`public-openapi-v0.1.json`由现有FastAPI公开业务路由导出，完整应用另含诊断路由。运行服务的 `/docs`、`/redoc`、`/openapi.json` 已关闭；离线导出不依赖这些 HTTP 入口。

身份及写请求遵守真实HTTP状态和`success/code/msg/data`外壳；`msg`仅供本地化，客户端按稳定code处理。业务路径`/v1/*`，内部`/internal/v1/*`，前端接收`/internal/cache/v1/*`，协同`/ws/v1/collab/{draft_id}`；开发代理`/api`不进入业务契约。

资料、Key及账号/应用管理仅Session+精确Origin/CSRF；Bearer用于已明确登记能力的业务API，失败不借用Cookie。私有资料/凭据不进公开快照，接口、迁移与部署顺序见[账号指南](../docs/development/accounts.md)。
