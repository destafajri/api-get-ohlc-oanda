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
def test_research_context_returns_only_redacted_user_context(
    client: TestClient,
) -> None:
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
    app.dependency_overrides[get_settings] = _research_settings

    response = client.get(
        "/research/oanda-context",
        headers={"X-Research-Token": "research-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "environment": "practice",
        "upstream": "api-fxpractice.oanda.com",
        "country": "SG",
    }
    assert user.called
    assert user.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert "private-user" not in response.text
    assert "private@example.com" not in response.text
    assert "12345678" not in response.text


@respx.mock
def test_research_context_rejects_missing_country(
    client: TestClient,
) -> None:
    respx.get("https://api-fxpractice.oanda.com/v3/users/@").mock(
        return_value=Response(200, json={"userInfo": {"userID": 12345678}})
    )
    app.dependency_overrides[get_settings] = _research_settings

    response = client.get(
        "/research/oanda-context",
        headers={"Authorization": "Bearer research-secret"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_oanda_response"
    assert "12345678" not in response.text
