会话、账号与权限
================

浏览器登录
----------

1. 读取 ``GET /v1/auth/session`` 获取当前会话状态及 ``csrf_token``。
   浏览器保留服务端设置的 HttpOnly Cookie，并按部署要求携带凭据。
2. 向 ``POST /v1/auth/login`` 发送登录请求。请求使用当前会话的 ``X-CSRF-Token``；
   登录策略需要验证码时携带 ``captcha``。字段见 ``app/modules/auth/schemas.py`` 的 ``LoginInput``。
3. 若响应 ``mfa_required`` 为真，向 ``POST /v1/auth/login/second-factor`` 提交第二步验证。
   客户端以最新响应返回的 Cookie 与 CSRF 状态为准。
4. 后台用 ``GET /v1/admin/session`` 核对工作台会话。退出用 ``POST /v1/auth/logout``，
   与其他受保护写请求一样带当前 CSRF 令牌。

路由在 ``app/modules/auth/router.py``，会话创建、刷新和撤销在同目录的 ``service.py``。
Cookie 路径为 ``/v1``，域、Secure 和 SameSite 属性取决于部署。
登录后仍回到登录页时，依次确认前后端真实地址、精确 Origin、浏览器收到的 Cookie 和请求的 CSRF 令牌。

账号访问与邮箱
--------------

账号管理员通过 ``/v1/admin/access/settings`` 读取和设置开放方式、密码找回与发信配置。

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - 功能
     - 实现入口
   * - 注册与邀请
     - ``registration_router.py``、``registration_service.py`` 和 ``access_router.py``。
       注册模式为 ``closed``、``invite`` 或 ``open``。
       先读 ``GET /v1/auth/registration/options``，再按策略请求发信并确认验证码。
   * - 密码找回
     - ``recovery_router.py`` 和 ``recovery_service.py``。
       ``/v1/auth/password-recovery/options`` 说明是否开启，请求接口以统一的接收状态响应。
   * - 账户资料和管理员操作
     - ``accounts_router.py``、``accounts_service.py`` 和 ``email_verification_service.py``。
       调整账号邮件功能时一并检查 :doc:`notification-delivery`。
   * - 两步验证
     - ``two_factor_service.py`` 和 ``two_factor_schemas.py``。
       登录第二步与账户内设置、恢复码管理各自校验会话上下文。

能力、资源与程序访问
--------------------

``app/modules/auth/permissions.py`` 的 ``Actor`` 统一表达浏览器会话或 API Key 身份。
``content:read``、``content:write``、``content:publish`` 分开判定，能力范围可为 ``own`` 或 ``all``。
路由检查决定能否进入用例，服务再核对作者、目标资源和状态。检查路径见
``business_auth.py`` 与 ``resource_access.py``。

``credentials_router.py`` 提供 API Key 获取、撤销及内省接口。
客户端携带 ``Authorization: Bearer`` 调用明确支持此认证方式的业务操作。
新增自动化接口时分别验证 Key 自身范围、所属应用和账户权限，再核对资源范围。
验证场景见 :doc:`captcha`，业务响应及对外契约见 :doc:`api`。
