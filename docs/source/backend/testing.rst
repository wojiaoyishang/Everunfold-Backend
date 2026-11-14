检查与测试
==========

基础检查
--------

使用 Python 3.12 和 uv 在后端根目录执行。依赖版本由 ``pyproject.toml`` 和 ``uv.lock`` 管理。

.. code-block:: bash

   uv sync --locked
   uv lock --check
   uv run --locked ruff check .
   uv run --locked ruff format --check .
   uv run --locked mypy
   uv run --locked pytest

测试源文件位于 ``tests/`` 和 ``tools/wordpress_import/tests/``。修改单个模块时，可先定向运行相关测试。例如，后端插件路由和 AI 写作分别由 ``tests/test_backend_plugins.py`` 和 ``tests/test_ai_writing.py`` 覆盖。接口变更需要核对授权、错误状态和前端依赖的契约。

隔离数据库和多进程测试
----------------------

需要真实数据库和 Redis 的检查由 ``RUN_DATABASE_TESTS=1`` 启用。先把示例配置复制到由 Git 忽略的 ``config.test.local.toml``，将 PostgreSQL 指向独立的 ``everunfold_test``，为 Redis 与队列配置测试地址和命名空间。预先创建测试数据库后，可显式写入最新迁移。涉及接收器时，为 ``EVERUNFOLD_TEST_FRONTEND`` 指定已安装依赖、版本匹配的前端检出目录。

.. code-block:: bash

   CONFIG_FILE=config.test.local.toml uv run --locked alembic -c migrations/alembic.ini upgrade head
   RUN_DATABASE_TESTS=1 uv run --locked pytest tests/test_database_init.py
   EVERUNFOLD_TEST_FRONTEND=/path/to/Everunfold-Frontend \
     RUN_DATABASE_TESTS=1 uv run --locked pytest tests/test_settings_processes.py

个别用例会创建、清理临时数据库，需要测试角色拥有对应权限。用例跳过时查看 pytest 的结果摘要和原因。迁移、权限、协同和发布边界的修改应同时验证后台真实进程，尤其要覆盖并发写入、旧数据读取、重复投递以及接收器故障后的恢复。

.. toctree::
   :maxdepth: 1

   troubleshooting
