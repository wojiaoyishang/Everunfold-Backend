协同编辑与版本
==============

建立协同连接
------------

1. 在具备草稿编辑权限的会话中，用 ``POST /v1/admin/content/collaboration/ticket`` 请求一次性票据。
2. 建立 ``/ws/v1/collab/{draft_id}`` 的 WebSocket，在首帧完成票据校验。
   反向代理应允许 WebSocket Upgrade 并保留原路径。
3. 按协同契约同步 Y.Doc。更新持久化后才收到确认，断线重连时从服务端已提交状态继续。
4. 协同保存或发布的提交入口为 ``/v1/admin/content/collaboration/commit``。
   服务会针对提交边界重新检查内容权限，发布或撤回需要发布能力。

路由、连接处理和提交检查对应 ``app/modules/content/collaboration/router.py``、
``socket.py``、``service.py``。调整协同协议时同步核对
``contracts/collaboration-v1.schema.json`` 及 ``tools/export_collaboration_contract.py``。

源码租约与版本分支
------------------

``source_leases.py`` 处理源码编辑租约。取得、续期、提交和释放由
``/v1/admin/content/collaboration/source/`` 下的接口处理。
租约状态改变后，重新核对当前状态与本地编辑结果。

版本查询、离线分支和合并动作共享 ``/v1/admin/content/versions/`` 前缀，
实现见 ``app/modules/content/version_router.py`` 与 ``version_service.py``。
分支上传后，按合并准备响应返回的标识处理冲突并提交决议。
合并后先核对草稿，再通过常规发布流程更新读者内容。

开发验证
--------

修改锁、租约或合并行为时，覆盖旧版本、并行改动、租约超时及分支合并。
用两个编辑客户端验证断线重连是否保留已确认修改。进程、文档和接口覆盖分别见
``tests/test_collaboration_processes.py``、``tests/test_collaboration_document.py`` 和
``tests/test_collaboration_contract.py``。
