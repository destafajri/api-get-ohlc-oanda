import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import Settings, get_settings
from app.main import app


def _research_settings() -> Settings:
    return Settings(
        oanda_token="test-token",
        oanda_environment="practice",
        research_context_token="research-secret",
    )


@respx.mock
def test_research_context_requires_dedicated_token(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_settings] = _research_settings

    response = client.get("/research/oanda-context")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "research_context_unauthorized"
    assert not respx.calls


@respx.mock
def test_research_context_returns_only_redacted_division_context(
    client: TestClient,
) -> None:
    accounts = respx.get("https://api-fxpractice.oanda.com/v3/accounts").mock(
        return_value=Response(
            200,
            json={
                "accounts": [
                    {"id": "001-011-00000000-001", "tags": []},
                    {"id": "001-011-00000000-002", "tags": []},
                ]
            },
        )
    )
    app.dependency_overrides[get_settings] = _research_settings

    response = client.get(
        "/research/oanda-context",
        headers={"X-Research-Token": "research-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "environment": "practice",
        "upstream": "api-fxpractice.oanda.com",
        "accounts": [{"site_id": "001", "division_id": "011"}],
    }
    assert accounts.called
    assert accounts.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert response.headers["Cache-Control"] == "no-store"
    assert "00000000" not in response.text


@respx.mock
def test_research_context_rejects_malformed_account_id(
    client: TestClient,
) -> None:
    respx.get("https://api-fxpractice.oanda.com/v3/accounts").mock(
        return_value=Response(200, json={"accounts": [{"id": "leaky-account-value"}]})
    )
    app.dependency_overrides[get_settings] = _research_settings

    response = client.get(
        "/research/oanda-context",
        headers={"X-Research-Token": "research-secret"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_oanda_response"
    assert "leaky-account-value" not in response.text
