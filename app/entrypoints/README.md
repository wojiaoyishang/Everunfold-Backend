# 进程入口

- `api.py`：FastAPI业务与协作入口，按唯一模块注册器装配。
- `worker.py`：Celery独立任务入口，执行设置/公开内容可靠投递、API Key应用回调，以及已登记的到期内容发布；不运行FastAPI的`router_init(ctx)`。
- `scheduler.py`：Redis租约保证同命名空间单活，周期投递上述具名tick；不加载业务任务、不进行业务HTTP或修改发布投影。到期计划与Outbox事实保存在PostgreSQL，队列tick重复、过期或丢失不丢失已登记工作。

三个角色共用后端发布批次与配置，通过独立进程运行。迁移必须显式执行，启动不自动变更数据库。实际运行检查见`docs/handoff/STATUS.md`及容器部署指南，不以某个进程启动替代业务验收。
