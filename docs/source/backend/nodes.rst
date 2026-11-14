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

1. 保存站点设置时，``app/modules/settings/service.py`` 更新有效修订，形成公开设置投影。
   私有设置不能通过调整内容投递接口进入公开快照。
2. 内容发布由 ``app/modules/content/publication.py`` 形成读者投影并登记可靠投递。
   设置与内容分别拥有修订或发布代次。
3. Worker 在事务提交后投递。重试时按节点、站点来源、资源和版本对照回执。
   已入队表示后端记录了请求，已应用表示节点确认完成。
4. 若界面仍显示旧内容，在 「节点状态与维护」 对照内容代次、设置修订和接收器状态。
   节点重新同步使用 ``POST /v1/admin/frontend-nodes/{node_id}/cache/rebuild``；
   内容、设置与整个节点可按目标范围处理。先确认节点启用并绑定接收器。

投递实现从 ``app/modules/settings/delivery.py`` 和
``app/modules/content/publication_models.py`` 中定位。
节点收到撤回或转为受限的新内容后，以新状态为准。

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
