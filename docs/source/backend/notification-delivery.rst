通知策略、模板与发送通道
========================

从事件到投递
------------

业务代码发出事件，目录定义模板变量、受众及适用通道，管理员策略选择接收规则。
``app/modules/notifications/definitions.py`` 定义事件结构，``catalog.py`` 组合内置与插件声明。
注册验证码、邮箱验证和找回密码各自使用认证队列，仍通过 ``routing.py`` 读取事件对应的发信配置。
下图适用于评论待审、新增用户等通知事件。

.. figure:: /_static/diagrams/backend-notifications.svg
   :width: 100%
   :class: flow-diagram
   :alt: 通知事件随业务事务提交。Worker 按规则匹配收件人和模板，再根据通道写入站内收件箱，或认领记录并发送外部通知。

   普通通知事件按收件人和通道生成各自的投递结果。

1. 业务服务用 ``events.publish_event()`` 登记事件，按 ``dedupe_key`` 去重，随当前事务提交。
2. Worker 执行 ``core.notifications``。``service.py`` 按启用的策略匹配有效收件人，
   同一事件对同一收件人及通道只生成一条投递。渲染模板时选用收件人的首选语言。
3. 站内通知与投递记录一并写入数据库。邮件或插件通道由 ``delivery.py`` 认领，发送前再次核对收件人与通道。
   投递失败、配置变化导致跳过、外部结果不明确分别可记为 ``failed``、``suppressed`` 或 ``unknown``。
   发送租约到期时，如果外部结果仍未知，记录为 ``unknown`` 并留待人工核对，避免自动重复发送。

配置步骤
--------

1. 通过 ``GET /v1/admin/notifications/state`` 读取状态和可配置目录。
   建立通知路由前先确认事件要求的收件人及平台。
2. 用 ``PATCH /v1/admin/notifications/routes`` 保存通知策略，
   用 ``PATCH /v1/admin/notifications/templates`` 保存事件模板。
   账号安全事件可能固定收件人或通道，还要求管理账号访问的权限。
3. 邮件发送配置走 ``/v1/admin/settings/mail``；试发和查询试发状态分别使用
   ``/v1/admin/settings/mail/test`` 和 ``/v1/admin/settings/mail/test/status``。
   在测试环境使用测试接收地址，确认发信配置、模板、受众和投递结果。
4. 站内收件箱使用 ``GET /v1/admin/notifications/inbox``。
   用户个人偏好使用 ``/v1/account/notifications/preferences``；
   例如订阅评论回复邮件前需要验证邮箱。

验证策略与投递结果
~~~~~~~~~~~~~~~~~~

1. 在测试站点给「评论待审核」启用站内规则和邮件规则，指向测试账号及已验证的邮箱。
   提交一条会进入待审核状态的评论。
2. 用该账号打开站内收件箱，再到「投递记录」对照事件和平台。邮件预期为 ``sent`` 时同时检查
   测试邮箱、模板语言和链接；SMTP 接受邮件后才会记录 ``sent``。
3. 验证去重、发送配置变更及租约到期时，准备 :doc:`testing` 中的独立测试数据库，运行以下用例。

   .. code-block:: bash

      RUN_DATABASE_TESTS=1 uv run --locked pytest tests/test_notifications.py

新事件或平台
------------

后端插件在 ``setup()`` 里通过 ``registry.notifications.register_event(definition)``
声明稳定事件 ID，通过 ``registry.notifications.register(platform)`` 增加平台。
按事件实际承载的数据声明模板变量。设计发送逻辑时明确数据库提交后的投递边界，
在受控测试中覆盖通道停用、无收件人、重复投递和外部服务失败。
邮件设置、通知和投递测试分别见 ``tests/test_mail_settings.py``、
``tests/test_notifications.py`` 与 ``tests/test_mail_delivery.py``。
