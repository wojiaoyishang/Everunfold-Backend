![Everunfold · 星夜中展开的书页](docs/assets/everunfold-banner.webp)

# Everunfold Backend

Everunfold 是一个可自行部署的博客与轻量 CMS。这个仓库使用 FastAPI、PostgreSQL、Redis 和 Celery，负责身份权限、内容与发布、异步任务和节点同步。数据库保存草稿和发布记录，Worker 将公开快照与设置投递到配套前端。

[配套前端仓库](https://github.com/wojiaoyishang/Everunfold-Frontend) · [后端开发手册](https://wojiaoyishang.github.io/Everunfold-Backend/) · [前端开发手册](https://wojiaoyishang.github.io/Everunfold-Frontend/)

**当前处于预发布准备阶段，正在做初步测试。** 如果想试用，可以先在独立环境跑通登录、编辑、发布和节点同步。配套提交、验证清单与已知限制见 [预发布说明](docs/source/release/prerelease.rst)。

这是一个 **vibe coding 项目**。项目发起人 [@wojiaoyishang](https://github.com/wojiaoyishang) 确定整体架构、模块边界和关键设计取舍，使用 AI 辅助实现，并根据代码与联调结果继续优化。

## 主要功能

- **内容与发布**管理文章、说说、页面、术语和媒体，保存协同草稿与版本，支持定时发布。
- **账号与互动**处理权限、邮箱验证、找回密码、两步验证、评论审核及通知投递。
- **运行与扩展**提供节点同步、公开查询缓存、站点维护和可信后端插件。API、Worker 和 Scheduler 可分别运行。

## 本地开发

使用 Python 3.12、uv、PostgreSQL 和 Redis。先创建专用开发数据库，在仓库根目录执行 `uv sync --locked`，再参照 [应用配置示例](config.example.toml) 和 [节点配置示例](frontend-nodes.example.toml) 建立本地配置。配置数据库、Redis、Broker、初始管理员密码和前端连接后，显式初始化数据库。

```bash
uv run --locked python init.database.py --config config.toml --no-create-database
```

随后分别启动 FastAPI、Celery Worker 和 Scheduler。默认 API 端口为 `8001`，可从 `http://127.0.0.1:8001/health/ready` 检查数据库、Redis 和迁移状态。五个前后端进程、方向凭据和首次同步的完整命令见 [本地快速开始](https://wojiaoyishang.github.io/Everunfold-Backend/welcome/quickstart.html)。

```bash
uv run --locked ruff check .
uv run --locked pytest
```

隔离数据库、多进程测试和故障排查见 [后端开发手册](https://wojiaoyishang.github.io/Everunfold-Backend/backend/index.html) 与 [运维手册](https://wojiaoyishang.github.io/Everunfold-Backend/operations/index.html)。

## 源码入口

| 路径                                                             | 职责                                    |
| ---------------------------------------------------------------- | --------------------------------------- |
| [`app/entrypoints/`](app/entrypoints/)、[`app/core/`](app/core/) | API、Worker、Scheduler 的入口和运行基础 |
| [`app/modules/`](app/modules/)、[`app/plugins/`](app/plugins/)   | 业务模块、插件运行时和内置扩展          |
| [`migrations/`](migrations/)、[`contracts/`](contracts/)         | Alembic 数据库迁移和版本化接口契约      |
| [`tools/`](tools/)、[`tests/`](tests/)                           | 运维命令、契约导出和测试                |

后台操作见 [站点使用指南](https://wojiaoyishang.github.io/Everunfold-Backend/usage/index.html)。手册源码和发布方法见 [RST 文档](docs/source/index.rst) 与 [文档构建说明](docs/source/release/documentation.rst)。技术决策与交接记录见 [设计索引](docs/README.md)。

## 致谢与许可

- 感谢 **WordPress**。内容管理、媒体库和后台编辑流程参考了 WordPress，原项目采用 GNU GPL v2 或更新版本。 [源码仓库](https://github.com/WordPress/wordpress-develop) · [许可声明](https://wordpress.org/about/license/)
- 感谢 **solstice23 的 WordPress Argon 主题**。Everunfold 的默认阅读主题借鉴了 Argon 1.3.5 的横幅、侧栏和阅读交互，以 React 和 Next.js 实现。Argon 上游声明采用 **GNU GPL v3.0**。 [源码仓库](https://github.com/solstice23/argon-theme) · [LICENSE](https://github.com/solstice23/argon-theme/blob/v1.3.5/LICENSE)
- **Elementor** 的组件面板和实时画布为可视化编辑器提供了交互参考。RST 手册的目录和示例组织参考了 **XiaoHeiHook** 和 **Pear Admin Flask**。 [Elementor](https://github.com/elementor/elementor) · [XiaoHeiHook](https://github.com/wojiaoyishang/XiaoHeiCat) · [Pear Admin Flask](https://gitee.com/pear-admin/pear-admin-flask)

Everunfold 的项目级许可证尚待确定。前端随附来自 Argon 1.3.5 的贴纸，其中「花！」的来源项目声明使用 CC BY 4.0。主题背景和演示图片另有来源记录，部分素材的再分发授权仍待核实。 [花！素材库与许可](https://github.com/k4yt3x/flowerhd#开源许可) · [前端主题素材清单](https://github.com/wojiaoyishang/Everunfold-Frontend/blob/main/src/themes/argon/assets/sources.json) · [演示图片来源](https://github.com/wojiaoyishang/Everunfold-Frontend/blob/main/public/reference/sources.json)
