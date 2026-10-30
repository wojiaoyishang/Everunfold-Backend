import pytest

from app.core.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    return Settings.model_validate(
        {
            "BACKEND_BASE_URL": "http://127.0.0.1:8000",
            "database": {"url": "postgresql+asyncpg://test:test@127.0.0.1:1/everunfold_test"},
            "redis": {"url": "redis://127.0.0.1:1/0", "namespace_scope": "test-unit"},
        }
    )
