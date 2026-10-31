"""Explicit database/bootstrap entrypoint; never imported by server startup."""

from app.modules.auth.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
