通知策略、模板与发送通道
========================

从事件到投递
------------

业务代码发出事件，目录定义模板变量、受众及适用通道，管理员策略选择接收规则。
``app/modules/notifications/definitions.py`` 定义事件结构，``catalog.py`` 组合内置与插件声明。
``service.py`` 与 ``routing.py`` 根据规则安排发送，``delivery.py`` 负责异步投递。
站内通知与邮件使用同一事件定义，但各自有交付结果。

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

新事件或平台
------------

后端插件在 ``setup()`` 里通过 ``registry.notifications.register_event(definition)``
声明稳定事件 ID，通过 ``registry.notifications.register(platform)`` 增加平台。
按事件实际承载的数据声明模板变量。设计发送逻辑时明确数据库提交后的投递边界，
在受控测试中覆盖通道停用、无收件人、重复投递和外部服务失败。
邮件设置、通知和投递测试分别见 ``tests/test_mail_settings.py``、
``tests/test_notifications.py`` 与 ``tests/test_mail_delivery.py``。
