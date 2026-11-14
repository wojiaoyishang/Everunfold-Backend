文章、说说与发布
================

数据从哪里来
------------

``app/modules/content/draft_models.py`` 保存可编辑草稿和修订，
``models.py`` 中的 ``Content`` 表达已发布资料，
``publication_models.py`` 保存可投递给前端节点的发布和投递记录。
读取草稿从 ``draft_service.py`` 查看当前版本和编辑模式，
读者列表和详情从 ``router.py``、``service.py`` 查找。

编辑与发布
----------

1. 用 ``POST /v1/admin/content/detail`` 读取详情和当前修订，列表使用
   ``/v1/admin/content/query``。可用类型由 ``GET /v1/admin/content/types`` 返回。
2. 根据编辑器模式读取和提交内容。基本创建与保存接口为
   ``/v1/admin/content/create``、``/v1/admin/content/save``。
   已启用协同的草稿经 :doc:`collaboration` 的提交边界保存。
3. 请求模型支持时，用 ``expected_revision`` 表达当前编辑的版本，
   用 ``operation_id`` 表达独立写操作。重试同一请求沿用该 ID。
   编号被其他修改使用或修订有冲突时保留当前输入，重新比对后再提交。
4. 如需公开，执行发布动作。它会检查发布权限、作者或资源范围和正文状态。
   从节点投递回执确认公开发布实际应用。

``draft_schemas.py`` 定义写入字段和修订，``draft_service.py`` 执行状态转换并检查收据，
``publication.py`` 建立公开投影并登记投递。

定时发布和翻译
--------------

``/v1/admin/content/schedule`` 安排发布，``/v1/admin/content/cancel-schedule`` 取消计划。
``scheduled_publication.py`` 领取到期任务后重新检查执行账号、权限和草稿状态，
再生成发布版本。撤回走 ``/v1/admin/content/withdraw``，读者面以节点最新应用的版本为准。

``translation_router.py`` 将查询、创建、关联、解除关联和标记审阅放在
``/v1/admin/content/translations/``。不同语言拥有各自草稿与发布修订，翻译组记录关联关系。
验证翻译修改时，分别核对后台关联和公开切换结果。
