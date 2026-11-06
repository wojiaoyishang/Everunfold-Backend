# 显式数据库迁移

项目根执行`uv run --locked alembic -c migrations/alembic.ini upgrade head`。连接来自显式加载器的`CONFIG_FILE`/`DATABASE_URL`，凭据不进Alembic配置；服务启动不调用迁移或`create_all()`。当前head为`0024_page_internal_metadata`。

完整顺序见`versions/`：0001–0018已覆盖公开内容、登录、设置、媒体、草稿/发布、协同、字体、短代码、版本合并和多语言。账号阶段新增：

| 版本 | 范围 |
| --- | --- |
| 0019_account_management | 资料、账号版本/授权、会话/密码审计及无凭据操作回执 |
| 0020_api_keys_and_applications | 用户Key摘要、接入应用和可靠回调Outbox |
| 0021_private_user_avatars | 本人头像图库、可调整限制和用户当前头像引用 |
| 0022_user_permission_presets | 可配置身份预设、修订/软删除、初始编辑/审稿人和用户应用来源快照 |
| 0023_managed_pages | 停用缺省源码页面、独立公开应用值、可信节点注册清单 |
| 0024_page_internal_metadata | 不公开的页面内部JSON、独立修订及初值 |

从空环境建库、迁移并创建首个管理员，可运行`uv run --locked python init.database.py --config config.toml`。只在目标库不存在时尝试创建；已有用户不改密码或提升角色。首次本地缺省凭据仅供初始化，部署通过`EVERUNFOLD_ADMIN_PASSWORD`提供密码。账号中心已支持改密，用户管理支持创建/重置；详见[账号指南](../docs/development/accounts.md)。

迁移通过PostgreSQL会话级advisory lock串行化；`alembic check`可检查漂移。集成测试只允许专用测试库/命名空间，进程P0另建受控临时库并清理，不连接WordPress原库。`downgrade`是操作者明确执行的破坏性方向，启动、回退和测试不自动执行。
