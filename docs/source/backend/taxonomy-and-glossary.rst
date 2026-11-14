分类、标签与术语
================

分类与标签的受控变更
--------------------

管理路由位于 ``app/modules/content/management_router.py``。
``/v1/admin/content/taxonomy/query`` 返回候选条目和版本，分类支持父级，标签是平面集合。
公开读取走 ``GET /v1/taxonomies``。内容行与分类、标签之间的关系由
``taxonomy_service.py`` 处理，客户端应使用返回的稳定 ID。

1. 读取源分类及目标分类的最新版本。
2. 修改、合并或移除会影响已有草稿和发布版本。先调用
   ``/v1/admin/content/taxonomy/preview``，核对草稿、已发布、计划发布和子项计数，
   在保存请求中带上新的 ``impact_token``。
3. 用 ``/v1/admin/content/taxonomy/save`` 提交。请求的修订与预览应描述同一次变更。
   如果依赖关系变化会收到冲突，重新读取影响再决定是否操作。
4. 最后查看公开分类归档和节点发布状态，检查关联是否按预期更新。

术语和正文挂钩
--------------

术语功能由内置插件 ``everunfold.glossary`` 提供，源码为 ``app/plugins/glossary/``。
``plugin.py`` 分别声明公开读、后台读和后台写权限。
``api.py`` 提供公开索引、详情和内容匹配，``schemas.py`` 描述条目、别名、匹配规则和挂钩模型。

以正文提示为例，可先在后台预览术语匹配，再为具体目标配置挂钩；
读者端用插件的 ``/for-content/{content_id}`` 或 ``/hooks/for-target`` 接口读取适用数据。
公开路由全名前缀为 ``/v1/plugins/everunfold.glossary``，条目管理接口在同前缀的 ``/admin/`` 下。
排查某篇内容中的误匹配时，分别核对条目是否公开、匹配规则、目标范围和前端渲染。

元数据与测试
------------

术语可使用共用的 SEO 字段，模型见 ``app/modules/content/seo.py``；
Canonical 覆盖值使用站内路径。处理 SEO 或索引可见性改动时，核对实际公开路径和各语言版本。
业务回归从 ``tests/test_content_management.py`` 和 ``tests/test_glossary.py`` 开始。
