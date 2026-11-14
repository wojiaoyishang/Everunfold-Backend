站点维护、数据导出与导入
========================

检查服务健康
------------

``GET /health/live`` 回答 API 进程是否存活。``GET /health/ready`` 还检查数据库、
Redis 和迁移版本。管理接口 ``GET /v1/admin/maintenance/health`` 进一步检查任务队列、
媒体目录、邮件和查询缓存等用途。实现见
``app/modules/maintenance/health.py`` 和 ``app/entrypoints/api.py``。

导出和导入内容
--------------

原生维护包格式为 ``everunfold-content``，当前版本为 1。
导出可按数据类型、状态和日期限制范围，类型及请求体见 ``app/modules/maintenance/schemas.py``。
实际导出字段由 ``records.py`` 决定。需要做可恢复备份时，单独保存数据库、媒体和部署密钥。

1. 以管理员浏览器会话通过 ``POST /v1/admin/maintenance/export`` 取得数据包。
2. 目标站点用 ``POST /v1/admin/maintenance/import/preview`` 检查数据。
   根据 ``create``、``skip``、``conflict`` 和 ``blocked`` 分别处理将创建、
   已存在、冲突和受阻条目。
3. 保留预览响应的摘要。在确认后，用 ``POST /v1/admin/maintenance/import/apply``
   发送同一数据包和预览摘要。变更数据包后先生成新的预览。
4. 用 ``GET /v1/admin/maintenance/history`` 核对导入回执，检查新建的草稿和依赖项。

维护业务由 ``app/modules/maintenance/planning.py`` 和 ``service.py`` 实现。
测试可从 ``tests/test_maintenance_processes.py`` 开始，先覆盖预览、应用与重复提交。
