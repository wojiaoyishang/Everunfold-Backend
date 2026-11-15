<p align="center">
  <img src="docs/assets/everunfold-banner.webp" alt="Everunfold · 星夜中展开的书页" width="880">
</p>

<h1 align="center">Everunfold Backend</h1>

<p align="center">
  <strong>后端短暂离线，已同步的公开内容仍可阅读。</strong><br>
  Everunfold 是一套可自行部署的博客与轻量 CMS。
</p>

<p align="center">
  <a href="https://wojiaoyishang.github.io/Everunfold-Backend/welcome/quickstart.html">快速开始</a> ·
  <a href="https://wojiaoyishang.github.io/Everunfold-Backend/">后端手册</a> ·
  <a href="https://github.com/wojiaoyishang/Everunfold-Frontend">前端仓库</a> ·
  <a href="https://wojiaoyishang.github.io/Everunfold-Frontend/">前端手册</a>
</p>

<p align="center">
  <strong>预发布 · 初步测试中</strong> · <a href="docs/source/release/prerelease.rst">预发布说明</a>
</p>

## 从发布到阅读

后台发布时，后端生成带版本的公开快照，并交给任务进程投递到前端节点。节点校验、落盘后返回回执，站长可在后台看到目标节点已经应用的版本。

前端节点已有完整且有效的快照时，即使暂时连不上后端，Next.js 仍可从本地快照渲染已公开的文章和页面。登录、发评论和其他实时操作仍需连接后端。 [发布、投递与节点回执](https://wojiaoyishang.github.io/Everunfold-Backend/backend/nodes.html)

## 这个仓库负责什么

- **内容与发布。** FastAPI 和 PostgreSQL 管理内容、草稿与版本，发布时在同一事务中登记待投递记录。
- **账号与互动。** 提供权限、邮箱验证、密码找回、两步验证、评论审核和通知投递。
- **运行与扩展。** API、Celery Worker 和 Scheduler 独立运行，支持节点同步、公开查询缓存、站点维护和后端插件。

## 关于开发

Everunfold 是一个 **vibe coding 项目**。项目发起人 [@wojiaoyishang](https://github.com/wojiaoyishang) 确定架构、模块边界和关键设计，借助 AI 实现，并通过代码审查和联调持续优化。

## 部署与本地运行

前后端各自提供统一的根目录入口，生成并维护根目录 `config.yaml`。密钥自动生成且可编辑，PostgreSQL / Redis 与后端就绪情况可在彩色配置助手中检测。

| 部署方式        | 在本仓库根目录执行    |
| --------------- | --------------------- |
| Docker 从零构建 | `./setup.sh --docker` |
| 不使用 Docker   | `./setup.sh --native` |

直接运行 `./setup.sh` 也可先选择部署方式。完整教程按这两种路径分开说明，见[部署教程](deploy/README.md)；镜像构建文件见 [docker/](docker/README.md)。已有配置先导入再保存，原密钥保持稳定；无需逐项编辑多个运行文件。

开发检查：

```bash
uv run --locked ruff check .
uv run --locked pytest
```

首次同步和运行验收见[快速开始](https://wojiaoyishang.github.io/Everunfold-Backend/welcome/quickstart.html)。

## 源码入口

| 路径                                                             | 职责                                    |
| ---------------------------------------------------------------- | --------------------------------------- |
| [`app/entrypoints/`](app/entrypoints/)、[`app/core/`](app/core/) | API、Worker、Scheduler 的入口和运行基础 |
| [`app/modules/`](app/modules/)、[`app/plugins/`](app/plugins/)   | 业务模块、插件运行时和内置扩展          |
| [`migrations/`](migrations/)、[`contracts/`](contracts/)         | Alembic 数据库迁移和版本化接口契约      |
| [`tools/`](tools/)、[`tests/`](tests/)                           | 运维命令、契约导出和测试                |

后台操作见 [站点使用指南](https://wojiaoyishang.github.io/Everunfold-Backend/usage/index.html)。手册源码和发布方法见 [RST 文档](docs/source/index.rst) 与 [文档构建说明](docs/source/release/documentation.rst)。技术决策与交接记录见 [设计索引](docs/README.md)。

开发交接见 [AI 开发协作约定](docs/handoff/AGENTS.md)和[当前状态](docs/handoff/STATUS.md)。

## 致谢与许可

- 感谢 **WordPress**。内容管理、媒体库和后台编辑流程参考了 WordPress，原项目采用 GNU GPL v2 或更新版本。 [源码仓库](https://github.com/WordPress/wordpress-develop) · [许可声明](https://wordpress.org/about/license/)
- 感谢 **solstice23 的 WordPress Argon 主题**。Everunfold 的默认阅读主题借鉴了 Argon 1.3.5 的横幅、侧栏和阅读交互，以 React 和 Next.js 实现。Argon 上游声明采用 **GNU GPL v3.0**。 [源码仓库](https://github.com/solstice23/argon-theme) · [LICENSE](https://github.com/solstice23/argon-theme/blob/v1.3.5/LICENSE)
- **Elementor** 的组件面板和实时画布为可视化编辑器提供了交互参考。 [源码仓库](https://github.com/elementor/elementor)

Everunfold 的项目级许可证尚待确定。前端随附来自 Argon 1.3.5 的贴纸，其中「花！」的来源项目声明使用 CC BY 4.0。主题背景和演示图片另有来源记录，部分素材的再分发授权仍待核实。 [花！素材库与许可](https://github.com/k4yt3x/flowerhd#开源许可) · [前端主题素材清单](https://github.com/wojiaoyishang/Everunfold-Frontend/blob/main/src/themes/argon/assets/sources.json) · [演示图片来源](https://github.com/wojiaoyishang/Everunfold-Frontend/blob/main/public/reference/sources.json)
