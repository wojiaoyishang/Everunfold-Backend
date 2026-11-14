AI 写作助手
===========

插件与入口
----------

内置插件 ID 为 ``core.ai-writing``。后端插件入口为
``app/plugins/ai_writing/plugin.py``，以浏览器会话注册接口，并登记生成任务。
在后台启用插件后，还需按站点实际需求打开 AI 写作功能并配置服务商及适用字段。
编辑器在生成对话中使用其当前字段和服务商的可用状态。

管理员配置
----------

1. 以具备站点设置写入能力的会话读取
   ``GET /v1/plugins/core.ai-writing/configuration``。
2. 在后台填写服务商的标识、名称、模型与服务地址，如有需要写入 API Key。
   提交配置使用 ``PUT /v1/plugins/core.ai-writing/configuration``，
   非空服务商列表需要指定默认项。配置读取报告是否已保存密钥。
3. 用 ``POST /v1/plugins/core.ai-writing/test`` 提交连接测试，
   在生成状态入口查看结果。编辑器自己的字段与服务商可用状态通过
   ``GET /v1/plugins/core.ai-writing/availability`` 获取。

请求字段见 ``app/plugins/ai_writing/schemas.py``，持久配置和业务检查在
``service.py``。私有服务来源通过部署环境变量 ``EVERUNFOLD_AI_PROVIDER_ORIGINS``
配置。模型密钥加密读取站点后端部署的应用主密钥，引用来自
``auth.application_master_key_env``。部署或迁移时保留原有引用和值。

一次生成如何执行
----------------

编辑器提交 ``POST /v1/plugins/core.ai-writing/generations`` 后返回请求状态。
``service.py`` 核对当前场景、字段和资源资格，准备并保存生成请求；
``worker.py`` 领取请求，在重新确认当前配置及权限后调用模型。
客户端从 ``GET /v1/plugins/core.ai-writing/generations/{identity}``
读取原请求人的状态或结果，再由编辑器确认是否写回字段。

遇到排队持续不动时，先看 Scheduler 和 Worker 是否就绪。
服务商错误时区分当前站点配置、模型名、网络出口与具体服务商响应。
验收时使用测试服务商并覆盖生成成功、敏感内容投影、权限改变及队列失败。
插件测试位于 ``tests/test_ai_writing.py``。协议导出见 :doc:`api`。
