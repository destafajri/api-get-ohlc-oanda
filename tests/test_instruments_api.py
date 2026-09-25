import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import Settings, get_settings
from app.main import app


@respx.mock
def test_get_instruments_returns_normalized_account_instruments(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        oanda_token="test-token",
        oanda_environment="practice",
        oanda_account_id="101-001-12345678-001",
    )
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/accounts/"
        "101-001-12345678-001/instruments"
    ).mock(
        return_value=Response(
            200,
            json={
                "instruments": [
                    {
                        "name": "XAU_USD",
                        "displayName": "Gold",
                        "type": "METAL",
                    },
                    {
                        "name": "EUR_USD",
                        "displayName": "EUR/USD",
                        "type": "CURRENCY",
                    },
                ]
            },
        )
    )

    response = client.get("/instruments")

    assert response.status_code == 200
    assert response.json() == {
        "environment": "practice",
        "count": 2,
        "instruments": [
            {
                "name": "XAU_USD",
                "display_name": "Gold",
                "type": "METAL",
            },
            {
                "name": "EUR_USD",
                "display_name": "EUR/USD",
                "type": "CURRENCY",
            },
        ],
    }
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert response.headers["Cache-Control"] == "no-store"


def test_get_instruments_requires_account_configuration(client: TestClient) -> None:
    response = client.get("/instruments")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "instrument_list_not_configured",
            "message": "OANDA account ID is required to list instruments.",
        }
    }


@respx.mock
def test_get_instruments_rejects_malformed_oanda_response(
    client: TestClient,
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        oanda_token="test-token",
        oanda_environment="practice",
        oanda_account_id="101-001-12345678-001",
    )
    respx.get(
        "https://api-fxpractice.oanda.com/v3/accounts/"
        "101-001-12345678-001/instruments"
    ).mock(return_value=Response(200, json={"instruments": "not-a-list"}))

    response = client.get("/instruments")

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "invalid_oanda_response",
            "message": "OANDA returned an unexpected response.",
        }
    }
