# 后端交接状态

更新：2026-10-31（Asia/Shanghai）。在线阶段：**基础后端与公开阅读**。应用版本 0.2.0，公开 API/快照契约 0.1.0；另有前台展示草案 0.2.0-preview.1。

## 本轮交付与范围

用户确认实施通用前台功能，说说优先；高中日记、关于及特色页面留到后续自定义页面，后台主题设置和整体导入后置。旧轮只读盘点限制已结束，但本轮没有迁移数据库或执行整体导入。

- 本仓库 `29f198a`：新增 app/modules/content/presentation_schema.py、tools/export_presentation_contract.py、contracts/presentation-v0.2.schema.json 与 5 项针对性测试。
- 草案 type=article/shuoshuo/page，含评论开放、互动计数和访问状态；说说允许无标题，受限投影禁止正文/摘要/媒体映射，定时状态必须有开放时间。草案未装配 HTTP 路由，没有替换在线 0.1 kind 或修改数据库。
- 前端从该确定提交生成类型、Schema 副本和 SHA256，使用唯一适配层接入旧数据和四条虚构说说。首页混排/说说列表详情、评论格式与贴纸、图片/代码/脚注工具、搜索、推荐分享及外观偏好已实现。细节见 [ADR 0007](../decisions/0007-shuoshuo-and-reader-features.md)、[内容类型衔接](../architecture/content-types.md) 和 [本轮验证](frontend-reader-verification.md)。
- 正式说说导入、type 生产迁移、验证码/审核/身份/权限与真实评论点赞尚未实现，不以草案或前端演示冒充正式业务。

本轮后端 5 项草案测试、Ruff 格式/检查通过；mypy 检查两个新增源码文件通过。前端 39 项测试、lint/typecheck/格式、23 项页面 HTTP 回归和独立副本生产构建通过。浏览器连接器要求重登，本机 computer-use 被用户物理 Esc 中止，已停止应用输入；本轮未做桌面/手机视觉交互验收。没有重跑旧后端数据库/多进程验收，不将前端结果代替后端检查。

## 恢复上下文

先读本页、docs/README.md、development/protocol.md、architecture/constraints.md、上述 ADR 0007 和验证记录。旧 ADR 0001–0006 及浏览器证据作为历史保留，原始基线在 docs/baseline/，不依赖临时附件或聊天恢复进度。

开始前检查分支与工作区、保留协作者修改。用户要求分阶段本地提交，**远程 push 由用户执行，代理不得 push**。后端端口 **8001**、前端 **3000**，不占用其他项目的 8000；本轮未停止后端，未更改后端实际配置。

## 已实现后端与现有数据

- 严格服务端 TOML 配置与显式环境覆盖，未知字段拒绝，敏感字段脱敏；真实配置均忽略提交。
- SQLAlchemy asyncpg 模型、独立请求 Session、显式 Alembic 迁移/迁移互斥；服务启动不自动改库。
- DB/Redis 生命周期，/health/live 与 /health/ready 分开；ready 检查数据库、Redis 和迁移版本。
- 静态 manifest.toml、唯一 router_init(ctx)，models/schemas/repository/service/router 分责。
- /v1/site、/v1/contents、/v1/contents/resolve、/v1/contents/{id}/comments、/v1/taxonomies；分页、分类标签、简单字面搜索、只读评论、统一 JSON 外壳与真实状态码。
- 公开读取仅 published + public；草稿/撤回/受限/不列出记录不进入公开列表/统计/解析。业务 OpenAPI 与 Schema 在 contracts/。
- 只读 WordPress 提取、节点级媒体/内链处理、同事务幂等导入、一致快照导出及原子替换；只作为受控本地工具，非完整生产同步。
- 现有试导入 78 文章、9 普通页面、11 评论、78 标签、7 分类；排除 4 商城系统页和 3 密码保护内容。快照 generation=3；312 媒体约 55.3 MB 留在前端忽略目录。未迁 WordPress 账户及评论邮箱/IP/UA，原备份和线上站点未改。

历史公开阅读验收：Ruff、格式、mypy（当时 34 文件）、锁文件检查、30 项测试（含独立 PostgreSQL 测试库和导入测试）、8001 就绪/API/422/404/CORS/OpenAPI 外壳通过。详见 public-reading-verification.md；这些属于旧阶段证据，并非本轮重跑。

## 尚未实现

- 账号/Session/API Key、CSRF、正式权限、后台写入、修订/审核、协同/Yjs。
- 正式 type 迁移、说说导入、真实评论写入/审核/验证码/通知、点赞与密码/定时解锁。
- Worker/Scheduler/Outbox relay、内部真实 IP AND Token 校验、缓存通知/撤回/乱序/换代恢复、镜像/CDN 及完整 P0 故障验收。预留配置不表示已启用。
- 中文分词/全文排序及完整旧插件语义迁移；当前搜索只为公开阅读的简单字面匹配。

## 下一步与协作

1. 用户按新前台验证记录检查说说/评论/图片/设置及窄屏交互，再修实际反馈。当前浏览器中止不得被当成验收通过。
2. 前台定稿后确定生产 type 契约和兼容窗口，同步数据库、查询、导入器、公开快照、统计及前端生成客户端；显式迁移，禁止只改生成文件。
3. 全量导入另行安排，先只读盘点说说/原 URL/媒体/评论关系；不要为展示样例运行导入器。
4. 正式写入在身份/权限/审核设计明确后落实业务、审计、Outbox 同事务，再逐项实现可靠任务、缓存与协同并验收相应 P0。

前端项目 E:\Programming\Node\Everunfold-Frontend。app/core 为基础设施，app/modules/content 为业务，contracts 为权威契约，migrations 显式迁移，tools 显式操作，docs 为跨上下文交接。公共说明/ADR 与原始基线哈希已核对两仓库同步；仓库 STATUS 各自维护。实际配置、SQL、备份、用户数据、.venv 不提交。仅本地提交，没有推送或部署。
