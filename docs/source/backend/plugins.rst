开发后端插件
============

后端插件是与应用一起部署的可信 Python 代码。插件只需在 ``app/plugins/<目录>/plugin.py`` 导出 ``plugin`` 对象。目录名使用字母开头的小写 Python 标识符，插件 ID 使用独立而稳定的命名空间。插件的其他模块和资源按用途组织。

注册最小插件
------------

以下示例可放在 ``app/plugins/example_dev_note/plugin.py``。``setup()`` 只登记能力，数据库操作留在请求和任务中。

.. code-block:: python

   from app.plugin_sdk import PluginRegistry, ShortcodeSource, define_plugin


   def setup(registry: PluginRegistry) -> None:
       registry.shortcodes.register(
           ShortcodeSource(
               name="exampledev:note",
               title="开发笔记",
               kind="container",
               html='<aside class="dev-note">{{content}}</aside>',
               css=".dev-note{padding:1em;border:1px solid currentColor}",
           )
       )


   plugin = define_plugin(
       id="example.dev-note",
       title="开发笔记",
       version="1.0.0",
       setup=setup,
   )

插件启用后，在支持短代码的内容中调用。

.. code-block:: text

   {{< exampledev:note >}}
   这里是笔记正文。
   {{< /exampledev:note >}}

在宿主代码中可对照 ``app/plugins/basic_shortcodes/plugin.py`` 和 ``app/plugins/example_cards/plugin.py``。需要依赖其他插件时，用 ``requires`` 声明稳定 ID。渲染模板使用宿主的 HTML 与 CSS 处理规则。

扩展点
------

.. list-table::
   :header-rows: 1
   :widths: 46 54

   * - 注册方法
     - 用途
   * - ``registry.shortcodes.register(source)``
     - 登记代码维护的短代码和参数。
   * - ``registry.routes.register(router, ...)``
     - 注册 FastAPI 路由，在插件命名空间内使用会话或能力权限。
   * - ``registry.captcha.register(provider)``
     - 注册后端验证码校验提供方。
   * - ``registry.locales.register(locale, messages)``
     - 登记插件文案，读取时使用带插件 ID 的键。
   * - ``registry.notifications.register_event(definition)``
     - 登记通知事件、可用变量和受众模式。
   * - ``registry.notifications.register(platform)``
     - 登记通知发送平台。
   * - ``registry.tasks.register(definition)``
     - 登记有限期任务或常驻任务，入口通过 ``模块:函数`` 引用。
   * - ``registry.cache_events.register(handler)``
     - 订阅已提交的公开缓存版本事件。

HTTP 接口按 ``/v1/plugins/<插件 ID>/`` 挂载。注册默认使用 ``access="session"``，如需权限约束可以声明 ``access="permission"`` 和 ``permission="settings:read"`` 等能力。请求函数通过 ``PluginRequest`` 读取当前身份和 ``AsyncSession``。宿主负责插件状态、鉴权与提交，处理函数复用请求事务。

为上面的开发笔记插件添加一个配置读取入口时，可定义响应模型和路由，再在原有 ``setup()`` 中调用 ``registry.routes.register(router, access="permission", permission="settings:read")``。登记并启用后，请求 ``GET /v1/plugins/example.dev-note/metadata``。

.. code-block:: python

   from fastapi import APIRouter

   from app.plugin_sdk import JSONResponseSuccess, PluginRequest, PublicModel, PublicResponse


   class NoteMetadata(PublicModel):
       plugin_id: str


   router = APIRouter()


   @router.get("/metadata", response_model=PublicResponse[NoteMetadata])
   async def read_metadata(ctx: PluginRequest) -> JSONResponseSuccess:
       return JSONResponseSuccess(data=NoteMetadata(plugin_id=ctx.plugin_id))

宿主内的完整示例见 ``app/plugins/route_example/plugin.py`` 和 ``api.py``。

有限期任务由 Worker 执行。``TaskDefinition`` 的任务 ID 应属于插件命名空间，可用 ``parameters`` 绑定 Pydantic 模型。常驻任务由 Scheduler 启动的子进程执行，通过 ``ResidentContext`` 收到停止事件并保存检查点。实现可对照 ``app/plugins/runtime_example/``。

缓存回调是接收 ``CacheEvent`` 的异步函数。投递可能重试，业务侧应按事件 ID 去重，并用来源和修订识别过时事件。事件携带公开版本与受影响路径等元数据，节点应用结果通过回执查询。

本地检查与部署
--------------

1. 在后端根目录运行清单检查，确认插件被发现且声明有效。

   .. code-block:: bash

      uv run --locked python -m tools.register_backend_plugins

2. 在目标实例部署插件，并重启读取该插件的进程。在相同部署配置下登记清单。

   .. code-block:: bash

      uv run --locked python -m tools.register_backend_plugins --config config.toml --apply

3. 在后台「插件管理」查找登记后的插件并启用。使用测试内容调用示例短代码，确认发布后的公开渲染。
   如果插件含路由，用具有所需权限的会话检查响应；如果含任务，到「运行与任务」核对执行结果。

停用时会保留插件数据。项目按目录追踪内置插件，日常开发可以在仓库内新增已忽略的目录。
独立分发的插件可单独维护源码。配置、密钥和动态数据应保存在插件目录之外。

.. toctree::
   :maxdepth: 1

   notification-delivery
   ai-writing
