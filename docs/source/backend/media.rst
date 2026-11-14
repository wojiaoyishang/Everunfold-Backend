媒体、字体与存储
================

从媒体库到文件
--------------

``app/modules/media/router.py`` 提供媒体管理与公开读取。
客户端先通过授权接口上传文件，再使用响应中的资源标识建立内容引用。

1. 用 ``POST /v1/admin/media/upload`` 上传原始文件字节，Content-Type 为
   ``application/octet-stream``，查询参数由 ``MediaUpload`` 定义。
2. 服务检查尺寸、媒体类型和文件内容。校验入口见 ``upload_validation.py``、
   ``media_validation.py``，图像处理见 ``image_processing.py``。
3. 用 ``/v1/admin/media/detail`` 查看资源，或者用
   ``/v1/admin/media/query`` 查看媒体库列表。发布的媒体由
   ``/media/{object_key:path}`` 按对象键读取。路由只返回就绪且未删除的对象。

``app/modules/media/storage.py`` 的 ``LocalObjectStorage`` 使用部署配置的
``[media].uploads_path``。应用使用数据库记录识别对象，运维需同步备份数据库与媒体目录。
公开基础地址和底层磁盘目录分别按其用途配置。

引用、删除与导出
----------------

变更已有资源前，检查哪些文章、页面或设置在引用。
通过 ``/v1/admin/media/remove-preview`` 查看影响，再按预览结果提交删除。
引用与删除逻辑见 ``references.py``、``removal_plan.py`` 和 ``removal.py``。
记录恢复由 ``/v1/admin/media/restore`` 处理。验证时同时查看媒体库、读者端和节点快照。

字体管理入口在 ``app/modules/media/fonts.py`` 和同模块的路由中。
公开样式 ``GET /v1/fonts.css`` 根据有效字体生成。
个人头像使用单独的账号管理接口，见 ``app/modules/auth/avatar_router.py``。
