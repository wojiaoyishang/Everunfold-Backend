# WordPress 公开阅读数据导入

这是显式执行的本地迁移入口，读取原备份、生成公开中间数据、导入已迁移的专用 PostgreSQL 数据库，并导出前端开发用公开快照。它不执行 SQL/PHP，不读取原用户账户，不启动任何后台迁移。

## 预检与提取

在后端根目录使用项目虚拟环境运行（也可用 `uv run --locked python`）。先 dry-run：

```powershell
.\.venv\Scripts\python.exe -m tools.wordpress_import --source 'E:\Wordpress全量备份\db_lovepikachu_20261030023000vimdw.sql' --backup-root 'E:\Wordpress全量备份' --dry-run
```

确认报告后，提取并只复制公开内容实际引用的被动媒体：

```powershell
.\.venv\Scripts\python.exe -m tools.wordpress_import --source 'E:\Wordpress全量备份\db_lovepikachu_20261030023000vimdw.sql' --backup-root 'E:\Wordpress全量备份' --media-root 'E:\Programming\Node\Everunfold-Frontend\public\uploads\wordpress' --output var/wordpress-public.json --report var/wordpress-public-report.json
```

`--dry-run` 不写文件、不复制媒体。实际中间数据和详细差异报告位于被忽略的 `var/`；原 SQL 和原媒体不会被改动。前端 `public/uploads/wordpress/` 也忽略提交，新的工作副本需重新提取，不能把它当成 Git 已携带的数据。

## 显式数据库导入与快照导出

先按项目运行说明创建专用开发库、填写本地配置并运行 Alembic 显式迁移，再执行：

```powershell
.\.venv\Scripts\python.exe -m tools.wordpress_import.load --config config.toml --input var/wordpress-public.json --export 'E:\Programming\Node\Everunfold-Frontend\var\public-snapshots\current.json'
```

只重新导出当前数据库的公开状态：

```powershell
.\.venv\Scripts\python.exe -m tools.wordpress_import.load --config config.toml --export 'E:\Programming\Node\Everunfold-Frontend\var\public-snapshots\current.json'
```

导入通过稳定 UUID 与来源 ID upsert；一次导入事务负责内容、分类标签关联、评论、搜索投影及站点配置。重复相同输入不会增加记录或资源代次；有实际变化才递增站点 generation。源代际 `source_epoch` 由数据库保存，重复导入不更换。导出调用与 API 相同的公开过滤和 DTO，在 REPEATABLE READ 事务中读取一致状态，随后通过独立 staging 文件原子替换目标。

该工具用于可信管理员的本地初始导入，不是公开写 API、完整发布/撤回流水线或生产恢复流程。当前不会替代数据备份，不会自动删除数据库中已存在但新输入缺失的内容；重新选择迁移范围时须先审阅差异，不能把源文件替换等同于完整同步。

## 公开边界与转换

- 仅导入无密码、`publish` 状态的 `post` 和普通 `page`；排除 WooCommerce 商店/购物车/结账/账户系统页，不激活电商功能。
- 只取允许公开内容下 `approved` 普通评论，保存公开作者名、纯文本正文、时间与同内容回复关系；不保存邮箱、IP、User-Agent、用户表、登录资料或凭据。
- 白名单读取站点名称、公开主题配置、菜单位置及公开媒体信息；PHP 序列化只支持 primitive/array，不实例化对象。中间产物不包含整个 options 表。
- 原路径按 permalink 与页面层级生成，冲突拒绝导入；不创建历史别名/301。正文已知同源公开链接在真实节点上改成当前相对路径，保留 query/hash；未知独立应用保留原站外链并列入报告。
- 资源只在 HTML 属性与真实 Markdown 链接目的地转换为 `site://media/{id}`，随内容保存媒体映射。普通文本、HTML code/pre、Markdown fenced/inline code 不改写。未支持的 Markdown reference link 保留原文，不猜测转换。
- 本地媒体限制类型、实际文件签名、单文件 32 MiB 和总量 256 MiB；拒绝目录穿越及 SVG/HTML/PHP 等主动内容。已存在哈希目标必须校验一致，不覆盖原文件。旧 JPEG 尺寸变体缺失而对应 WebP 存在时，使用现有 WebP 并记录。
- 旧短代码保持为字面源文本并报告，不执行 PHP/任意脚本；最终 HTML 安全过滤由可信前端渲染层负责。尚未实现旧插件组件适配、Elementor 视觉搭建和完整格式转换。
- 原 WordPress 头像/背景另存于前端 `public/reference/`，来源记录在 `sources.json`，签名查询参数未进入来源清单。

## 工具检查

```powershell
.\.venv\Scripts\python.exe -m pytest tools/wordpress_import/tests -q
.\.venv\Scripts\ruff.exe check tools/wordpress_import
.\.venv\Scripts\ruff.exe format --check tools/wordpress_import
.\.venv\Scripts\mypy.exe --explicit-package-bases tools/wordpress_import
```

检查覆盖 MySQL 真实转义/多行/NULL/二进制非白名单表、PHP 字节长度、代码区保护、媒体类型/路径约束及同源链接作用域。数据库幂等性已在专用开发库实际验证；完整迁移恢复与切站验收仍需按基线另外完成。
