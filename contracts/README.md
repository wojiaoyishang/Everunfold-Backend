# 契约发布目录

后端是API/WS/事件契约的唯一权威发布者，前端从确定提交生成类型/客户端并记录SHA；文件名中的早期版本不等于当前版本，以`x-everunfold-contract-version`为准。

| 产物 | 当前版本 | 范围 |
| --- | --- | --- |
| auth-v0.1.schema.json | 0.3.0 | Cookie Session、CSRF、当前身份/授权、强制改密状态 |
| accounts-v1.schema.json | 1.5.0 | 私有资料、会话/密码、用户/授权、身份预设、Key/应用与受限头像库 |
| api-key-protocol-v1.schema.json | 1.0.0 | 应用签名校验、最小响应、签名回调/ACK与缓存边界 |
| captcha-v1.schema.json | 1.5.0 | 验证码策略、挑战/阅读/评论校验与受限页面图片查看投影 |
| collaboration-v1.schema.json | 2.7.0 | 持久协同、逐内容图片查看、源码租约、光标/合并暂停及提交回执拥有者字段 |
| comment-likes-v0.1.schema.json | 0.2.0 | 匿名历史评论点赞 |
| content-access-v1.schema.json | 1.5.0 | 私有受限正文读取，包含逐内容图片查看设置 |
| content-admin-v1.schema.json | 1.20.0 | 草稿、语言/导航、逐内容图片查看/术语设置、修订/分支/合并与页面预览 |
| glossary-v1.schema.json | 3.7.0 | 术语索引/详情、特色图片/图片查看开关、按内容匹配与跨页面 DOM/脚本触发 |
| notifications-v1.schema.json | 1.4.0 | 通知模板、邮件品牌、模板变量、投递记录与当前账号 Inbox |
| operations-v1.schema.json | 1.1.0 | 评论查询、审核、工作台及按评论 ID 定位和仅待审核确认 |
| pages-v1.schema.json | 1.13.0 | 页面草稿与应用、可信类型、自定义页面模板及 URL 校验 |
| presentation-v0.2.schema.json | 0.2.0-preview.5 | 草稿展示预览（草案） |
| public-v0.1.schema.json | 0.14.0 | 完整公开快照、多语言导航与逐页图片查看/术语/验证码边界 |
| reader-preview-v0.1.schema.json | 0.1.0-preview.1 | 阅读演示指标（预览） |
| settings-v1.schema.json | 1.44.0 | 站点、主题、系统与节点设置，含 Redis 查询缓存报告 |

权威导出入口为`tools/export_*_contract.py`；例如`uv run --locked python -m tools.export_accounts_contract`。协议Schema与独立Node参考对应，见[接入指南](../docs/development/api-key-applications.md)。`presentation-v0.2.schema.json`和`reader-preview-v0.1.schema.json`仍分别标记preview，不把展示草案当作正式写接口。`public-openapi-v0.1.json`由现有FastAPI公开业务路由导出，完整应用另含诊断路由。

身份及写请求遵守真实HTTP状态和`success/code/msg/data`外壳；`msg`仅供本地化，客户端按稳定code处理。业务路径`/v1/*`，内部`/internal/v1/*`，前端接收`/internal/cache/v1/*`，协同`/ws/v1/collab/{draft_id}`；开发代理`/api`不进入业务契约。

资料、Key及账号/应用管理仅Session+精确Origin/CSRF；Bearer用于已明确登记能力的业务API，失败不借用Cookie。私有资料/凭据不进公开快照，接口、迁移与部署顺序见[账号指南](../docs/development/accounts.md)。
