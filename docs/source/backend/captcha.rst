验证码与访问策略
================

请求过程
--------

验证码用于明确配置的操作，例如登录、注册、阅读或评论。站点配置、内容规则和操作共同决定是否需要挑战。
``app/modules/captcha/service.py`` 的 ``required()``、``context()`` 负责判断规则，
``CaptchaService`` 负责签发或验证挑战。

1. 用 ``POST /v1/reader/captcha/challenge`` 为当前操作请求挑战。
   请求、返回类型及服务商字段见 ``app/modules/captcha/schemas.py``。
2. 客户端按挑战类型展示操作，随后在原业务请求传入验证码答案。
   例如，登录请求的可选字段名为 ``captcha``。
3. 按响应更新挑战和表单错误。验证通过后的业务仍需校验身份和资源权限。

配置与联测
----------

后台读取和保存配置使用 ``GET /v1/admin/captcha``、``PATCH /v1/admin/captcha``。
修改账号访问相关选项还须满足 ``access:manage``。
Google 和 Turnstile 提供方可参考 ``app/plugins/captcha_google/plugin.py``、
``app/plugins/captcha_turnstile/plugin.py``。联测时使用测试平台配置。

- 操作未出现挑战时，核对操作类型及生效的站点或内容策略。
- 验证无法提交时，确认挑战期限、答案类型以及当前页对应的目标资源。
- 评论或受限正文出错时，分别核对权限、验证码和内容密码的业务错误码。
- 修改验证策略后，执行 ``tests/test_captcha.py``，并在浏览器覆盖失败和重新提交。

受限正文与公开投影的关系见 :doc:`reader-access`。
