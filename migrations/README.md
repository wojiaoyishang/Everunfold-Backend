# 显式迁移预留目录

尚未定义最终 SQLAlchemy 模型、Alembic 配置或数据库 revision。下一阶段先交付约束设计，再编写与验证显式迁移；FastAPI、Worker、Scheduler 均不在启动时自动执行核心或插件迁移。

首个数据库迁移须在隔离的空 PostgreSQL 数据库验证，包括约束失败与发布事务原子性；不接触 WordPress 原库。
