数据、任务与节点
================

数据和迁移
----------

PostgreSQL 保存内容、草稿和授权，也保存正式修订、索引投影、任务与投递记录。表结构变更通过 ``migrations/versions/`` 中的 Alembic 修订交付。部署前按发布批次显式升级，所有 API 与任务进程使用与迁移版本匹配的后端代码。``app/core/database.py`` 中的 ``SCHEMA_REVISION`` 是就绪检查要求的版本。

.. code-block:: bash

   uv run --locked alembic -c migrations/alembic.ini current
   uv run --locked alembic -c migrations/alembic.ini upgrade head

``init.database.py`` 适合首次初始化数据库和管理员，也会执行迁移。已有实例的升级可直接调用 Alembic。开发数据迁移应先在隔离数据库验证，尤其要覆盖既有账号、旧设置、加密字段和任务队列中的记录。

公开内容与缓存
--------------

普通编辑器为每个草稿维护一个 Y.Doc。协同更新写入 PostgreSQL 并提交后才发送持久化确认，Redis 通知供其他进程追赶状态。发布动作从已确认的修订生成读者投影，并将可靠投递记录一并提交。

运行 Redis 用于 Celery、进程广播、临时状态和心跳。查询缓存使用独立的 ``[query_cache].url`` 或 ``QUERY_CACHE_REDIS_URL``，缓存站点统计、分类标签和常用公开内容列表。查询缓存键涵盖来源及设置和内容版本，定时开放边界会缩短有效期。缓存缺失或不可用时，公开读请求仍可查询 PostgreSQL。涉及正文和私有状态的读取保持各自的访问控制。

前端节点接收已发布的公开快照。发布、撤回或转为受限后以新的版本为准，接收器已知不可见时始终阻止旧正文回退。在前端后台「节点状态与维护」核对快照应用情况。查询缓存命中、未命中和读库统计在启用后的后端节点控制台（``/_nodes``）查看。

派发与恢复
----------

Scheduler 扫描已登记的计划并向 Celery 投递执行 ID。Worker 通过持久状态和执行租约认领，运行结束后写回结果。常驻任务由 Scheduler 管理的子进程维护连接。Redis 消息可能重复或丢失，恢复以 PostgreSQL 的计划和执行记录为准。

站点设置和公开内容发布在数据库事务中登记待投递记录。Worker 在事务完成后逐节点发送，记录重试和回执。开发消费者时应按稳定事件 ID 去重，并按来源、节点和资源版本决定处理顺序。HTTP 接收成功和节点最终应用是两个状态，可通过后台回执分别核对。

需要订阅缓存事件、登记任务或通知渠道时，请参阅 :doc:`plugins`。

.. toctree::
   :maxdepth: 1

   runtime-jobs
   nodes
   maintenance
