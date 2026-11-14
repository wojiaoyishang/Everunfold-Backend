故障定位
========

先确定故障发生在哪一层，再按实现入口核对状态。诊断时记录 HTTP 状态、业务错误码、受影响节点和修订；
复现记录使用测试数据，删去凭据、用户资料和正文。

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - 现象
     - 从这里查
   * - API 存活，读者页仍不可用
     - 对照 ``/health/ready`` 的数据库、Redis 和 schema 状态，
       再查 :doc:`nodes` 的公开快照版本。
   * - 登录后回到登录页，私有写入失败
     - 按 :doc:`accounts` 核对 Cookie、精确 Origin、CSRF 和能力范围。
   * - 保存成功，公开内容没有变化
     - 比对草稿版本、发布操作及前端节点回执，见 :doc:`article-lifecycle` 和 :doc:`nodes`。
   * - 协同无法建立或断线后冲突
     - 查看反向代理的 WebSocket 转发、当前票据与草稿权限，以及 :doc:`collaboration` 的提交边界。
   * - 邮件、同步或模型生成长时间排队
     - 用 :doc:`runtime-jobs` 的方式确认 Scheduler、Broker 和 Worker，
       再继续查询具体业务投递。
   * - 插件端点未加载
     - 按 :doc:`plugins` 检查目录、声明、安装清单、启用状态和会话权限。
   * - 前端类型无法生成
     - 核对契约文件和版本、后端来源提交，以及实际检出的 SHA。
       导出指令见 :doc:`api`。
