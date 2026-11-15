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

Swagger ``/docs``、ReDoc ``/redoc``、``/openapi.json`` 及 Swagger OAuth 回调入口均关闭，访问返回统一 JSON 404。路由与模型继续通过下方 CLI 离线导出，无需对外开放交互文档。开发代理可以在站点端接收带 ``/api`` 前缀的请求，并在转发时剥离该前缀。直接调用后端时使用 ``/v1/``。

业务 JSON 采用 ``success``、``code``、``msg`` 和 ``data`` 外壳。客户端按 HTTP 状态和稳定业务码分支，``msg`` 用于本地化展示。``code`` 是整数，``data`` 为 ``None`` 时可省略。成功、框架 404/405/422、鉴权/限流、CORS 预检、依赖 503 和未处理异常 500 都使用该外壳，并保留真实状态及必要响应头。非 JSON 的应用错误（例如文件范围请求的 416）也统一封装，不暴露内部异常内容。

``/_nodes/status``、``/_nodes/query-cache``、``/_nodes/manage`` 同样使用此结构，结果位于 ``data``，错误说明位于 ``msg``。节点控制台同步读取新结构；登录错误在原页面显示。

文件、样式表、HTML 页面、流、WebSocket、重定向及 HEAD/204/304 无正文语义按各自协议处理。反向代理或 HTTP 服务器在请求进入应用前产生的错误，需在对应服务配置，不能由应用捕获。

直接请求后端 ``GET /`` 返回 ``success=true``、``code=200``，``msg`` 为：

   当我离去的时候，让我的思想来到你的身边，正如那夕阳的余晖，映在寂静星空的边缘。

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
