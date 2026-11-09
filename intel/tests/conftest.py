import pytest
from fastapi.testclient import TestClient

from intel.config import Settings, get_settings
from intel.main import app


@pytest.fixture
def client_for():
    """Test client factory: client_for(mode="live", api_token="t", ...) overrides settings."""

    def make(**overrides):
        values = dict(
            mode="stub", api_token=None, anthropic_api_key=None, scan_model="m", judge_model="m", deadline_s=2.5
        )
        values.update(overrides)
        app.dependency_overrides[get_settings] = lambda: Settings(**values)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()
