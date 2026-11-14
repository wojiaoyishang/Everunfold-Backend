代码结构
========

Everunfold 后端使用 FastAPI、SQLAlchemy、PostgreSQL、Redis 和 Celery。入口按进程分开，业务模块可以在 API 和任务之间复用。

源码导览
--------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - 路径
     - 职责
   * - ``app/entrypoints/``
     - HTTP 和协同 WebSocket、Celery Worker、Scheduler 的启动入口。
   * - ``app/core/``
     - 配置、进程资源、模块装配、权限边界所需的公共基础设施，以及任务与插件加载。
   * - ``app/modules/auth/``
     - 浏览器会话、账号、授权、注册、找回、两步验证与 API Key。
   * - ``app/modules/content/`` 和 ``app/modules/pages/``
     - 文章、说说、草稿协同、发布和读者访问，以及页面、模板与版本。
   * - ``app/modules/settings/`` 和 ``app/modules/notifications/``
     - 站点与运行设置、节点登记和投递、站内通知及邮件。
   * - ``app/modules/media/``、``app/modules/comments/``、``app/modules/captcha/``、``app/modules/shortcodes/``
     - 媒体与引用、评论管理、验证码和短代码。
   * - ``app/modules/maintenance/``
     - 授权的站点健康检查、原生数据包导入预检及导出。
   * - ``app/plugin_sdk.py`` 和 ``app/plugins/``
     - 可信 Python 插件的公共入口和内置实现。
   * - ``migrations/``、``contracts/``、``tools/``、``tests/``
     - 数据库迁移、版本化契约、开发运维命令和测试。

HTTP 模块装配
-------------

``app.entrypoints.api.create_app()`` 调用 ``app/core/modules.py`` 中的装配函数。
装配器读取业务模块的 ``manifest.toml``，校验模块身份，再调用声明的 ``router_init(ctx)`` 并挂载返回的 ``APIRouter``。
例如 ``app/modules/content/__init__.py`` 将阅读、内容编辑、版本、协同和评论管理的子路由组装到同一个路由器。

具体的网络请求从 FastAPI 依赖获取会话、当前用户和语言。生命周期创建的 ``Runtime`` 提供数据库与 Redis 连接等进程资源。业务数据校验使用 Pydantic 模型，权限与事务由用例服务协调，持久化查询放在仓储或相应模块的数据访问函数中。

添加业务接口时，先定位已有模块，复用它的依赖与权限检查，然后补充请求模型、响应模型和测试。注册独立的扩展能力请使用 :doc:`plugins`。

进程分工
--------

API 进程负责请求鉴权、数据库事务和协同连接。Worker 领取已登记的任务，执行通知、内容同步和其他有限时长的业务工作。Scheduler 使用 Redis 租约维持同一命名空间内单活，负责派发计划、恢复待执行记录和管理常驻子进程。任务进程使用各自入口，API 模块的装配与生命周期始终属于 API。

了解数据、缓存、任务重试和节点同步的边界请阅读 :doc:`data-and-jobs`。

.. toctree::
   :maxdepth: 1

   modules
