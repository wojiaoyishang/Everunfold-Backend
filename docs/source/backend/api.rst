接口与契约
==========

请求入口
--------

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - 路径
     - 用途
   * - ``/v1/``
     - 浏览器和已授权客户端使用的业务接口。
   * - ``/v1/plugins/<插件 ID>/``
     - 可信后端插件登记的原生 HTTP 接口。
   * - ``/internal/v1/``
     - 后端的服务间接口，按实际连接来源和令牌鉴权。
   * - ``/ws/v1/collab/{draft_id}``
     - 草稿协同 WebSocket，通过授权 HTTP 接口取得一次性票据。
   * - ``/health/live``、``/health/ready``
     - 进程存活检查，以及数据库、Redis 和迁移版本的就绪检查。

FastAPI 提供 ``/docs`` 和 ``/openapi.json``，便于在开发环境检查部署中的路由。开发代理可以在站点端接收带 ``/api`` 前缀的请求，并在转发时剥离该前缀。直接调用后端时使用 ``/v1/``。

业务 JSON 采用 ``success``、``code``、``msg`` 和 ``data`` 外壳。客户端按 HTTP 状态和稳定业务码分支，``msg`` 用于本地化展示。文件、流、WebSocket 和无正文响应按各自协议读取。

浏览器与服务间身份
------------------

浏览器首先读取 ``GET /v1/auth/session`` 获取会话状态和 CSRF 令牌。登录以及后续受保护的写请求使用 HttpOnly Cookie、允许的 ``Origin`` 和与当前会话绑定的 ``X-CSRF-Token``。登录或会话变更时用响应返回的新状态更新客户端令牌。鉴权成功后还要按能力和资源范围判断是否允许读取或修改目标。

机器客户端在明确支持的接口中使用 ``Authorization: Bearer``。授权受所属用户、应用和 Key 的当前权限约束。带无效凭据的请求按原认证方式失败。浏览器控制面和账号管理通常使用 Session，具体接口以路由声明为准。

内部接口分别校验底层连接来源与对应方向的令牌。部署时应保证后端看到预期的直接连接来源，并将内部接口与公开入口隔离。私有会话、草稿及受限正文应使用授权接口，响应须保持私有缓存策略。

导出和联动
----------

后端的 ``contracts/`` 保存业务模型、WebSocket 和同步协议的版本化契约。精确版本读取 ``x-everunfold-contract-version`` 字段。独立的前端仓库根据 ``contracts.lock.json`` 固定的后端提交和摘要生成客户端类型。

1. 修改路由与请求、响应模型后，使用相应的导出器生成契约。下列命令依次生成业务 OpenAPI、
   页面契约和 AI 写作插件的 OpenAPI。

   .. code-block:: bash

      uv run --locked python -m tools.export_contracts
      uv run --locked python -m tools.export_pages_contract
      uv run --locked python -m tools.export_plugin_routes --plugin-id core.ai-writing

2. 在 ``contracts/`` 比对业务和页面契约的新增、删除或必填字段变化。插件的 OpenAPI 默认写入
   ``var/cache/plugin-contracts/``，发布插件对应类型时须使用匹配的构建产物。
3. 前端消费到契约时，更新其锁定的后端提交和文件摘要，再生成并检查前端类型。
   在开发环境从正常入口请求变更过的接口，分别验证成功响应和相应的认证、权限或输入错误。

.. toctree::
   :maxdepth: 1

   accounts
   captcha
