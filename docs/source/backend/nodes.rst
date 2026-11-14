站点设置、节点与缓存
====================

节点连接
--------

节点由稳定的节点 ID、前端公开地址、后端到接收器的地址和双向凭据引用关联。
``app/modules/settings/node_registry.py`` 将数据库中的节点记录与部署绑定组合；
迁入前从部署配置读取。``frontend_nodes_file`` 与内联 ``[frontends]`` 二选一。
实操示例见 :doc:`../welcome/quickstart`。

``GET /v1/admin/frontend-nodes`` 列出节点和状态，
``GET /v1/admin/frontend-nodes/{node_id}/cache`` 返回节点内容同步及接收器储存信息。
管理面变更节点登记时，底层连接来源仍由部署配置承担，
跨节点或跨站请求必须核对当前节点 ID 和站点来源标识。

公开设置与内容投递
------------------

.. figure:: /_static/diagrams/backend-publication.svg
   :width: 100%
   :class: flow-diagram
   :alt: 发布事务提交公开快照和投递记录。Scheduler 派发任务，Worker 认领记录并发送，收到匹配回执后标记已应用，失败时按策略重试。

   内容发布从后端事务到前端节点的应用回执。

1. 发布、撤回或移入回收站时，影响公开内容的变更由 ``app/modules/content/publication.py``
   生成新一代投影，并登记每个同步节点的 Outbox 记录和缓存事件。草稿修订、发布快照和这些记录
   同事务提交。定时发布由 Worker 运行 ``core.publication``，计划到期后重新检查发布权限，
   使用计划中保存的草稿修订生成发布快照。
2. Scheduler 取得 Redis 租约后协调数据库任务计划，将到期的 ``core.settings`` 执行编号交给 Celery。
   Worker 在认领并提交投递租约后发起 HTTP 请求。发送失败或回执校验失败时，按退避时间和尝试上限重试。
3. ``app/modules/settings/delivery.py`` 核对回执的节点、来源、资源、版本和内容摘要，通过后才写入
   ``applied_at``。保存站点设置会由 ``app/modules/settings/service.py`` 生成公开设置投影，复用同一条投递链路。
   设置与内容分别维护修订或发布代次。节点按最新已应用的发布处理公开、撤回和访问限制。

验证事务和同步结果
~~~~~~~~~~~~~~~~~~

1. 在测试实例修改并发布一篇公开文章，记下 ``GET /v1/admin/content/publication`` 中的 ``revision``。
   在「运行与任务」确认 ``core.settings`` 有运行记录。
2. 在「节点状态与维护」检查目标节点，或请求 ``GET /v1/admin/frontend-nodes/{node_id}/cache``。
   对照 ``sync`` 中的 ``public-content`` 状态与接收器储存状态，再从该节点打开阅读页。
3. 测试接收器短时离线时，确认已提交的发布修订留在后端。恢复接收器后等待自动重试；达到尝试上限时，
   对已启用且已绑定接收器的测试节点调用 ``POST /v1/admin/frontend-nodes/{node_id}/cache/rebuild``，
   请求体使用 ``{"scope":"public-content"}``。复查状态变为 ``applied`` 后刷新阅读页。

重建也可指定 ``public-settings``、``cache-policy`` 或 ``all``。公开设置投影由服务显式构造，站点私有配置仍保存在后端。

``tests/test_settings_processes.py`` 的进程联测覆盖发布回滚、重复发布请求和接收器重启后的投递恢复。

两类缓存
--------

.. list-table::
   :header-rows: 1
   :widths: 27 39 34

   * - 类别
     - 用途
     - 排查
   * - 后端公开查询缓存
     - 独立 Redis 保存分类、列表和站点统计等查询结果。
       ``app/core/query_cache.py`` 和 ``app/modules/content/query_cache.py`` 管理查询操作。
     - 查看后台缓存报告或启用后的后端节点诊断 ``/_nodes``。
       缓存缺失或不可用时，公开查询仍可读取 PostgreSQL。
   * - 前端公开快照
     - 接收已提交版本，按源和节点保存可渲染资料。
     - 对照前端储存状态、发布版本和同步回执。
       快照为后端数据的公开投影，生产备份包含原始业务数据库与媒体。

缓存事件订阅在 ``app/modules/settings/cache_events.py`` 实现。新增监听方时使用稳定事件编号去重。
验证定时开放、撤回和同步恢复时，可参阅 ``tests/test_settings_processes.py``、
``tests/test_query_cache_processes.py`` 和 ``tests/test_nodes_monitor.py``。
