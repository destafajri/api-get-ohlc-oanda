from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: Settings(
        oanda_token="test-token",
        oanda_environment="practice",
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
