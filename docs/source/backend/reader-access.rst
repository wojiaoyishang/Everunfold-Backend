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
受限完整正文走 ``POST /v1/reader/access``，实现为同目录的 ``access_router.py``。
服务根据目标内容和当前策略核对访问资格，按规则附加验证码检查。
修改内容投影时，分别验证管理预览、读者列表、公开详情、直接 API 和共享缓存。

正文的回复可见区或密码区由 ``app/modules/shortcodes/regions.py`` 解析。
发布层仅提供适合公开渲染的信息，受保护部分由专用接口按策略读取。
修改渲染时，核对页面源代码、公开快照和搜索摘要是否都遵守相同公开范围。

评论
----

``app/modules/comments/`` 实现评论查询、提交、审核和讨论上下文。
写入时根据目标页面或文章检查评论是否开放、访问资格及评论验证码。
公开接口按规则读取可见评论，后台可结合审核状态排查。
评论回复邮件经通知事件交付，见 :doc:`notification-delivery`。
