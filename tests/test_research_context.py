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
def test_research_context_requires_dedicated_bearer_token(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_settings] = _research_settings

    response = client.get("/research/oanda-context")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "research_context_unauthorized"
    assert not respx.calls


@respx.mock
def test_research_context_returns_only_redacted_account_context(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_settings] = _research_settings
    accounts = respx.get("https://api-fxpractice.oanda.com/v3/accounts").mock(
        return_value=Response(
            200,
            json={
                "accounts": [
                    {
                        "id": "001-011-12345678-001",
                        "mt4AccountID": 9999999,
                        "tags": ["private"],
                    },
                    {
                        "id": "001-011-12345678-002",
                    },
                ]
            },
        )
    )
    user = respx.get("https://api-fxpractice.oanda.com/v3/users/@").mock(
        return_value=Response(
            200,
            json={
                "userInfo": {
                    "username": "private-user",
                    "userID": 12345678,
                    "country": "SG",
                    "emailAddress": "private@example.com",
                }
            },
        )
    )

    response = client.get(
        "/research/oanda-context",
        headers={"Authorization": "Bearer research-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "environment": "practice",
        "upstream": "api-fxpractice.oanda.com",
        "country": "SG",
        "accounts": [{"site_id": "001", "division_id": "011"}],
    }
    assert accounts.called
    assert user.called
    assert accounts.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert user.calls.last.request.headers["Authorization"] == "Bearer test-token"

    serialized = response.text
    assert "12345678" not in serialized
    assert "private-user" not in serialized
    assert "private@example.com" not in serialized
    assert "9999999" not in serialized


@respx.mock
def test_research_context_rejects_malformed_upstream_account_id(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_settings] = _research_settings
    respx.get("https://api-fxpractice.oanda.com/v3/accounts").mock(
        return_value=Response(200, json={"accounts": [{"id": "malformed"}]})
    )
    respx.get("https://api-fxpractice.oanda.com/v3/users/@").mock(
        return_value=Response(200, json={"userInfo": {"country": "SG"}})
    )

    response = client.get(
        "/research/oanda-context",
        headers={"Authorization": "Bearer research-secret"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_oanda_response"
    assert "malformed" not in response.text
