托管页面与模板
==============

辨别页面来源
------------

托管页面保存地址、语言、设置、可编辑内容和已应用版本。前端插件也能登记页面类型，
由后端保存其页面实例和发布状态，前端组件按注册定义渲染。
实现入口为 ``app/modules/pages/registration.py`` 和 ``types.py``，节点库存负责汇报登记状态。

页面与模板的使用顺序
--------------------

1. 从 ``GET /v1/admin/pages/types`` 读取类型，确认所需编辑模式。
2. 用 ``POST /v1/admin/pages/save`` 保存指定修订。请求模型 ``SaveManagedPage`` 位于
   ``app/modules/pages/schemas.py``，包括 ``expected_revision`` 和 ``operation_id``。
   分别核对后台最新草稿和公开页当前版本。
3. 页面历史恢复入口为 ``/v1/admin/pages/{page_id}/versions/restore``。恢复后先检查草稿。
4. 应用模板前检查受影响页面。``templates.py`` 提供影响预览、版本恢复和应用。
   调用方应将影响预览返回的核验值和版本传给应用动作，清单发生变化时先更新预览。

页面与模板的修订由 ``versions.py`` 管理。渲染侧使用节点已应用的设置与快照，
后台通过授权 API 读取草稿。页面路径、翻译关系和依赖注册状态分别由
``paths.py``、``translations.py``、``availability.py`` 维护。

验证从 ``tests/test_managed_pages.py``、``tests/test_page_languages.py`` 和
``tests/pages_process_checks.py`` 开始。新页面不可见时，核对启用状态、语言路由、
模板应用结果及前端节点的插件清单。
