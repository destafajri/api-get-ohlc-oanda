import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import get_settings
from app.main import app


def test_health_does_not_require_oanda(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_openapi_docs_are_available(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/ohlc" in response.json()["paths"]


def test_missing_configuration_returns_safe_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OANDA_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    get_settings.cache_clear()

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/ohlc", params={"instrument": "XAU_USD", "granularity": "H4"}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_configured"


@respx.mock
def test_get_ohlc_normalizes_oanda_response(client: TestClient) -> None:
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={
                "instrument": "XAU_USD",
                "granularity": "H4",
                "candles": [
                    {
                        "complete": True,
                        "volume": 123,
                        "time": "2026-08-12T20:00:00.000000000Z",
                        "mid": {
                            "o": "3348.210",
                            "h": "3361.540",
                            "l": "3342.800",
                            "c": "3357.190",
                        },
                    }
                ],
            },
        )
    )

    response = client.get(
        "/ohlc", params={"instrument": "XAU_USD", "granularity": "H4", "count": 1}
    )

    assert response.status_code == 200
    assert response.json() == {
        "instrument": "XAU_USD",
        "granularity": "H4",
        "count": 1,
        "candles": [
            {
                "time": "2026-08-12T20:00:00Z",
                "open": "3348.210",
                "high": "3361.540",
                "low": "3342.800",
                "close": "3357.190",
                "volume": 123,
                "complete": True,
            }
        ],
    }
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"
    assert route.calls.last.request.url.params["price"] == "M"


def test_get_ohlc_validates_query(client: TestClient) -> None:
    response = client.get(
        "/ohlc", params={"instrument": "xauusd", "granularity": "H4", "count": 0}
    )

    assert response.status_code == 422


@respx.mock
def test_oanda_auth_error_does_not_leak_details(client: TestClient) -> None:
    respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(return_value=Response(401, json={"errorMessage": "secret upstream detail"}))

    response = client.get(
        "/ohlc", params={"instrument": "XAU_USD", "granularity": "H4"}
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "oanda_authentication_failed",
            "message": "OANDA authentication failed.",
        }
    }
