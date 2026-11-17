# ADR 0185 Celery 任务进程自动伸缩

状态：已实现。来源：2026-11-17 用户要求为两套部署向导增加自动伸缩和列表选择。

默认保持现有行为：新部署用 `solo` 固定单进程；现有 `prefork` 固定并发不变。两套向导以可键盘操作的列表选择执行池和并发策略。只有 `prefork` 可启用自动模式，默认区间 1–4。上下限独立填写，须满足 `1 ≤ 最小 ≤ 最大 ≤ 128`。

自动模式使用 [Celery 原生 autoscale](https://docs.celeryq.dev/en/v5.6.3/userguide/workers.html#autoscaling)。现有 YAML 的 `WORKER_CONCURRENCY` 保留，只有固定模式才传给 Celery。自动模式设置 `WORKER_AUTOSCALE`（`最大,最小`），由 Celery 在有任务时扩容并在空闲时回收进程，项目 Scheduler 的计划、队列和任务确认方式保持一致。不需要数据库迁移。

API 和前端仍按固定 worker 数启动。Uvicorn 的 [TTIN/TTOU](https://uvicorn.dev/deployment/#built-in) 可调整现有多进程池；[Gunicorn](https://gunicorn.org/signals/) 也支持信号控制，但仍要另写根据请求负载选容量的控制器。Next.js 宿主的 `node:cluster` 已管理同端口 Web 进程和单份同步检查；接入 [PM2](https://pm2.keymetrics.io/docs/usage/commands/) 和[社区伸缩模块](https://github.com/VeXell/pm2-autoscale) 不能替代应用原有的同步协调、写入顺序及退出逻辑。两种 Web 服务若需要自动扩缩，先评估拆分服务后的 [Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)；保持 Scheduler 独立且只运行一份。

验证：隔离 Redis 和 Celery 5.6.3 从 1 个子进程扩至 4 个，完成 8 个任务后缩回 1 个。两套配置界面以 86×28 终端验证键盘选择，旧镜像测试验证在替换现有容器前提示升级。
