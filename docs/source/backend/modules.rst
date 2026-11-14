模块装配与配置加载
==================

从请求定位代码
--------------

以读取站点设置为例，可以沿以下顺序找到实现。

1. 在 ``app/modules/settings/router.py`` 查找 ``GET /v1/admin/settings``。路由使用
   ``Authorized`` 依赖取得当前 ``Actor`` 和 ``SettingsService``。
2. 在 ``app/modules/auth/business_auth.py`` 查看路由对应的权限和认证方式。
3. 在 ``app/modules/settings/service.py`` 查看服务如何读取当前修订、构造公开设置与节点状态。
4. 在 ``app/modules/settings/schemas.py`` 核对请求、响应和用于导出契约的模型。
5. 在 ``tests/test_settings_contract.py`` 和 ``tests/test_settings_processes.py`` 查找契约及进程联测。

新增同类功能时，把参数解析、权限检查、事务规则放在相应层，复用应用的响应外壳。
业务服务明确事务提交点。外部邮件、模型调用、节点请求等操作由相应 Worker 执行，
避免持有业务事务锁时等待外部网络。

模块注册
--------

第一方模块示例为 ``app/modules/content/manifest.toml``。
其中 ``router_entrypoint = "app.modules.content:router_init"`` 指向模块入口，
``app/core/modules.py`` 加载标识、版本和入口，检查返回的 ``APIRouter``，再挂载到 FastAPI。
``app/entrypoints/api.py`` 的 ``create_app()`` 显式调用模块注册函数。
为现有模块增加子路由时，在模块的 ``__init__.py`` 复用这套装配流程。
可选扩展在 ``app/plugins/<目录>/plugin.py`` 暴露 ``plugin``，注册方式见 :doc:`plugins`。

配置的来源
----------

``app/core/config.py`` 的 ``load_settings()`` 按入口显式传入的文件、
``CONFIG_FILE``、根目录 ``config.toml`` 的顺序选择配置文件。
``config.example.toml`` 提供配置结构参考。环境变量由启动进程或部署工具注入。

.. list-table::
   :header-rows: 1
   :widths: 39 61

   * - 配置项
     - 作用
   * - ``BACKEND_BASE_URL``
     - 公开后端根地址，构造公开 URL，也作为部分来源校验的基准。
   * - ``DATABASE_URL``、``REDIS_URL``
     - 分别覆盖业务数据库和运行 Redis 的连接。
   * - ``CELERY_BROKER_URL``、``CELERY_RESULT_BACKEND_URL``
     - 分别覆盖 Celery 的消息代理和结果后端。
   * - ``QUERY_CACHE_REDIS_URL``
     - 覆盖公开查询缓存的 Redis 地址。
   * - ``frontend_nodes_file``
     - 外置节点配置文件路径。也可以使用内联 ``[frontends]``。

外置节点配置文件的相对路径以主配置所在目录为准，顶层只包含 ``frontends`` 表。
节点 ID、实际来源、双向令牌和接收器关系见 :doc:`nodes`。

启动与就绪检查
--------------

.. figure:: /_static/diagrams/backend-readiness.svg
   :width: 100%
   :class: flow-diagram
   :alt: 先显式初始化或迁移，再启动 API。存活检查确认进程可响应，就绪检查按配置、数据库、Redis 和迁移版本返回 200 或 503。

   API 启动与接收业务流量前的就绪检查。

1. 选定配置和目标数据库。新实例执行 ``init.database.py``，完成迁移和初始数据准备。
   已有实例按 :doc:`data-and-jobs` 执行 Alembic 升级。部署命令和后端进程须指向同一套配置。
2. 启动 API。``app/entrypoints/api.py`` 在配置有效时创建 ``Runtime``，应用启动时不执行迁移。
   ``GET /health/live`` 返回 ``200`` 即表示 API 可以响应探针请求。
3. 请求 ``GET /health/ready``。配置有效时，探针同时检查数据库连接、运行 Redis 连接和
   Alembic 版本。后三项应在各自的超时内完成，版本必须匹配 ``app/core/database.py`` 中的
   ``SCHEMA_REVISION``。三项通过时返回 ``200``。

在开发环境启动 API 后运行以下命令，观察 HTTP 状态与响应体中的 ``data.checks``。

.. code-block:: bash

   curl -sS -i http://127.0.0.1:8001/health/live
   curl -sS -i http://127.0.0.1:8001/health/ready

就绪检查返回 ``503`` 时，按失败项定位。本地服务的运行 Redis 由 ``[redis]`` 配置，
``schema`` 单项失败时先用 ``uv run --locked alembic -c migrations/alembic.ini current``
核对所连接的数据库和迁移位置。另可运行 ``uv run --locked pytest tests/test_bootstrap.py``，
验证依赖不可用时仍可区分存活和就绪结果。
