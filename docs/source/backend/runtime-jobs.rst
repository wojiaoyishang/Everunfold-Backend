调度、执行与任务状态
====================

进程与数据
----------

请求进程负责创建业务记录。Scheduler 在当前 Redis 命名空间取得唯一租约后，
从数据库协调计划并派发到期任务。有限期任务由 Celery Worker 执行：
``app/entrypoints/worker.py`` 的统一入口接收执行编号，认领和完成写入
``app/core/jobs/models.py`` 中的任务记录。常驻任务由
``app/entrypoints/scheduler.py`` 监护子进程。

.. list-table::
   :header-rows: 1

   * - 需要确认
     - 实现入口
   * - 哪些任务存在，间隔与超时如何声明
     - ``app/core/jobs/definitions.py`` 及插件的 ``TaskDefinition``。
   * - 计划如何恢复、何时认领
     - ``app/core/jobs/service.py`` 的 ``reconcile()`` 和认领流程。
   * - 状态、心跳和日志如何更新
     - ``app/core/jobs/execution.py``、``telemetry.py`` 和 ``progress.py``。
   * - 后台如何读取与控制任务
     - ``app/modules/settings/runtime_router.py`` 和 ``progress_router.py``。

恢复与排查
----------

1. 在后台 「运行与任务」 或 ``GET /v1/admin/runtime`` 核对当前进程和执行记录。
2. 仍在排队时，检查 Scheduler 租约、数据库计划和 Broker，再确认 Worker 实际订阅的队列。
   一套命名空间只运行一个 Scheduler。
3. 运行中断时查看租约和持久状态。过期的执行记录可能为 ``unknown``，
   业务重试应遵循幂等边界。
4. 派发成功后若业务未应用，继续查看具体投递记录，例如 :doc:`nodes` 的公开版本回执
   或 :doc:`notification-delivery` 的发送结果。

新增任务时覆盖成功、重复派发、租约到期和外部服务失败。测试入口包括
``tests/test_runtime_manual.py``、``tests/test_runtime_parameters.py`` 和
``tests/test_runtime_processes.py``。
