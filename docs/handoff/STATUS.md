# 后端交接状态

更新：2026-10-30（Asia/Shanghai）。阶段：**公开阅读第一版，基础后端与本地公开数据闭环**。应用版本 0.2.0，公开契约 0.1.0。

后续前台交互动效阶段：前端已接原 WebM 背景与持久壳软导航，目录/样式分责见 frontend-ui.md 和 ADR 0003；浏览器实际验收已完成。本轮后端代码/数据库/契约未变，未停止用户 8001 实例，原后端检查是公开阅读阶段证据而非本轮重跑。

最新用户反馈要求先复刻原站体验：已完成原站浏览器对照，待实施清单见 [原站前台复刻审阅](original-site-ui-audit.md)。本次只更新审阅/交接文档；上阶段的功能验证不代表原站外观、滚动与加载中间态已通过复刻验收。

## 恢复上下文

先读本页、[文档索引](../README.md)、[开发协议](../development/protocol.md)、[公开阅读运行指南](../development/public-reading.md) 和 [公开阅读验证](public-reading-verification.md)；前台最新交互见 [动效验证](frontend-motion-verification.md)。原始附件在 docs/baseline/，保留字节与哈希；ADR 0001 是历史初始化范围，当前采用 ADR 0002/0003。

开始前检查 git status 与分支，保留协作者修改。**最新用户要求：按阶段本地提交，远程 push 由用户自行执行。** 后端本地端口为 **8001**，不要再占用其他项目的 8000。

## 已实现与实际数据

- 严格 TOML 加载器：项目根定位、CONFIG_FILE、明确环境覆盖、未知字段拒绝与 SecretStr 脱敏；不自动读取示例或 .env。真实 config.toml / config.test.local.toml 已填写并忽略提交。
- SQLAlchemy asyncpg 模型、独立请求 Session、显式 Alembic 迁移与迁移互斥；启动不修改 schema。开发库 everunfold_dev、测试库 everunfold_test 已迁移。
- 生命周期管理 DB/Redis；/health/live 与 /health/ready 分开，ready 检查数据库、Redis、迁移版本。
- 静态 manifest.toml 与唯一 router_init(ctx) 装配公开内容模块；models、schemas、repository、service、router 分责。
- /v1/site、/v1/contents、/v1/contents/resolve、/v1/contents/{id}/comments、/v1/taxonomies，支持分页、分类标签、简单中文/字面关键词搜索和只读评论。
- 公开投影统一筛选 published + public，草稿/撤回/受限/不列出内容不泄漏到列表、统计或解析；统一 JSON 外壳与真实状态码，OpenAPI 错误响应也采用相同外壳。
- 后端权威 JSON Schema 与业务 OpenAPI 在 contracts/；前端生成类型并记录来源版本/SHA256。
- 只读 SQL 白名单提取、媒体/内链节点处理、一次事务幂等导入、一致事务快照导出与原子替换；工具在 tools/wordpress_import/。
- 本地实际数据：78 文章、9 普通页、11 评论、78 标签、7 分类；排除 4 个商城系统页及 3 条密码保护内容。公开快照 generation=3、约 1.1 MB，已导出前端；312 项媒体约 55.3 MB 留在忽略目录。
- 相同输入重复导入已验证新增 0、更新 0、changed=0，generation 不增加。账户、邮箱/IP/UA 未迁入；原备份和线上站点未修改。

## 验证结果

Ruff、格式、mypy（34 个源码文件）、锁文件检查通过；30 项测试全部通过，含专用 PostgreSQL 测试库事务回滚和 10 项导入工具测试。真实网络 8001 的就绪、内容解析、公开评论、分页、422/404、CORS 与 OpenAPI 错误外壳通过。详细命令和限制见当前验证文档。

## 尚未实现

- 账号/Session/API Key、CSRF、权限管理、后台写接口、完整正式 Revision 与审核、协同/Yjs、任务管理。
- Celery Worker、Scheduler/Outbox relay、内部服务双重校验和 HTTP 缓存回调；预留配置不代表服务已启用。
- 完整 ICP、可靠撤回/乱序/换代恢复、镜像主动追赶、CDN 插件、生产日志脱敏与完整 P0 故障验收。
- 当前搜索是 PostgreSQL 可重建文档上的简单字面匹配，不声称实现中文分词/全文排序。
- 本次只做公开阅读试导入，不是所有受限内容/插件语义的最终迁移。未知短代码按字面安全降级，7 个未导入同源目标保留原站外链并报告。
- 公开阅读阶段的浏览器工具停止属于历史；后续前台动效阶段内置浏览器已可用，具体桌面/窄屏验证见 frontend-motion-verification.md。

## 下一步

1. 用户按运行指南检查前台外观和导航，再修视觉差异；前端已知边界以其交接为准。
2. 设计正式修订/审核与身份权限后再开放写入，补业务、审计和 Outbox 原子事务，不给当前只读模型直接加匿名写接口。
3. 冻结内部 ICP/WS 契约，再逐项实现可靠任务/回调、撤回与协同，执行对应真实多进程 P0。
4. 剩余迁移短代码、受限内容和外链按报告逐项处理，之后再制定生产发布、切站与恢复演练。

## 目录与协作

app/core 为基础设施，app/modules/content 为内容业务；migrations 为显式结构变更，contracts 为契约权威，tools 为显式操作命令，docs 为设计/交接。前端项目在 E:\Programming\Node\Everunfold-Frontend，主题与数据层分离。

阶段提交可在 git log 查看；origin/main 仍是上次初始化远程提交，当前阶段提交尚未推送。公共规范、环境、运行指南、ADR 与基线盘点在两仓库同步；每次后续交付更新本页和验证记录。
