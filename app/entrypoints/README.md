# 进程入口

- `api.py`：当前可运行的 FastAPI 骨架，仅 `/health/live`。
- `celery_app.py`：规划中的独立 Worker 入口，尚未创建；不得运行 FastAPI 的 `router_init(ctx)`。
- `scheduler.py`：规划中的独立单进程 Scheduler，尚未创建；不加载业务任务、不执行业务。兼任 Outbox relay 的建议需后续设计记录确认落地。

不存在的入口不能作为已完成服务启动。三个角色未来共用后端发布批次与基础包；迁移通过显式部署命令完成。
