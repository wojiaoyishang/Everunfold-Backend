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
