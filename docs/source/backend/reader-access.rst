公开读取、受限内容与评论
========================

公开读取
--------

``GET /v1/contents`` 提供公开列表，``GET /v1/contents/resolve`` 按公开路径解析内容，
``GET /v1/contents/{content_id}/comments`` 读取可见评论。
``app/modules/content/router.py`` 处理查询参数，``repository.py`` 和 ``service.py`` 读取公开投影。
前端节点也能用公开快照渲染已应用发布。

受限读取
--------

密码、定时开放等规则由 ``app/modules/content/access_policy.py`` 判定。
测试受限内容的读者流程可按以下步骤验证。

1. 在测试实例发布一篇密码保护文章。用匿名访问检查列表、详情和节点公开快照，
   确认标题仍然可见，摘要、正文和正文媒体引用均受保护。
2. 请求 ``POST /v1/reader/access``，用测试口令和策略要求的验证码验证。实现位于
   ``app/modules/content/access_router.py``。核对错误口令返回 ``403``，正确口令返回正文，
   且响应头包含 ``Cache-Control: private, no-store``。
3. 如正文使用回复可见区或密码区，先核对页面源代码、公开快照和搜索摘要只包含公开信息。
   再通过 ``POST /v1/reader/shortcode-region`` 验证区域访问。回复可见区要求当前登录账号在该页面
   或文章有一条已批准的评论；密码区验证独立的区域口令；所在内容需要解锁时先取得该内容的读取凭据。

区域提取与存储代码在 ``app/modules/shortcodes/regions.py``，受限读取位于同目录的 ``region_router.py``。

评论
----

``app/modules/comments/`` 实现评论查询、提交、审核和讨论上下文。
写入时根据目标页面或文章检查评论是否开放、访问资格及评论验证码。
公开接口按规则读取可见评论，后台可结合审核状态排查。
评论回复邮件经通知事件交付，见 :doc:`notification-delivery`。
