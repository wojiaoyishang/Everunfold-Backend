# 显式数据库迁移

从项目根执行 `uv run --locked alembic -c migrations/alembic.ini upgrade head`。
连接从显式配置加载器取得，支持 `CONFIG_FILE` / `DATABASE_URL`；不把凭据写进 Alembic 配置。
服务启动不调用 Alembic，不执行 `create_all()`。

`0001_public_read` 建立公开阅读所需内容、分类标签、评论、搜索派生文档及站点设置。
完整修订/审核、权限、协同、媒体管理、任务与 Outbox 模型仍待后续迁移，当前没有写入 API。
迁移命令通过 PostgreSQL 会话级 advisory lock 串行化；`alembic check` 可检查 ORM 与实际结构漂移。

初次迁移已在独立开发/测试空库验证。集成测试仅允许 `everunfold_test` 与 test scope，使用事务回滚保留数据库内容，不连接 WordPress 原库。
`downgrade` 是需要操作者明确执行的破坏性方向，正常应用启动、回退和测试均不自动执行。
