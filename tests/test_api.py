from datetime import datetime, timedelta, timezone

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import get_settings
from app.main import app


def test_root_is_crawler_readable(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "OANDA OHLC API" in response.text
    assert "/ohlc?instrument=XAU_USD&amp;granularity=H4&amp;count=100" in response.text
    assert 'href="/favicon.png"' in response.text
    assert 'href="/favicon.ico"' in response.text
    assert response.headers["Cache-Control"].startswith("public")


def test_favicons_are_available_and_cacheable(client: TestClient) -> None:
    png = client.get("/favicon.png")
    ico = client.get("/favicon.ico")

    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert png.headers["Cache-Control"] == "public, max-age=604800, immutable"
    assert ico.status_code == 200
    assert ico.headers["content-type"] == "image/x-icon"
    assert ico.content.startswith(b"\x00\x00\x01\x00")
    assert ico.headers["Cache-Control"] == "public, max-age=604800, immutable"

    assert client.head("/favicon.png").status_code == 200
    assert client.head("/favicon.ico").status_code == 200


def test_discovery_files_are_available(client: TestClient) -> None:
    robots = client.get("/robots.txt")
    sitemap = client.get("/sitemap.xml")

    assert robots.status_code == 200
    assert "User-agent: *" in robots.text
    assert "Allow: /" in robots.text
    assert "Sitemap:" in robots.text
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "/ohlc?instrument=XAU_USD&amp;granularity=H4&amp;count=100" in sitemap.text


def test_health_does_not_require_oanda(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_head_checks_do_not_require_oanda(client: TestClient) -> None:
    root = client.head("/")
    health = client.head("/health")
    ohlc = client.head(
        "/ohlc", params={"instrument": "XAU_USD", "granularity": "H4", "count": 100}
    )

    assert root.status_code == 200
    assert root.content == b""
    assert health.status_code == 200
    assert health.content == b""
    assert ohlc.status_code == 200
    assert ohlc.content == b""
    assert ohlc.headers["content-type"].startswith("application/json")
    assert ohlc.headers["Cache-Control"].startswith("public")
    assert ohlc.headers["Vercel-CDN-Cache-Control"]


def test_head_ohlc_reuses_query_validation(client: TestClient) -> None:
    response = client.head(
        "/ohlc", params={"instrument": "xauusd", "granularity": "H4"}
    )

    assert response.status_code == 422


def test_openapi_docs_are_available(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/ohlc" in response.json()["paths"]


def test_missing_configuration_returns_safe_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OANDA_TOKEN", raising=False)
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
    assert response.headers["Cache-Control"] == (
        "public, max-age=30, stale-while-revalidate=30"
    )
    assert response.headers["CDN-Cache-Control"]
    assert response.headers["Vercel-CDN-Cache-Control"]


def test_get_ohlc_validates_instrument(client: TestClient) -> None:
    response = client.get(
        "/ohlc", params={"instrument": "xauusd", "granularity": "H4"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("count", [0, 5001])
def test_get_ohlc_validates_count_bounds(client: TestClient, count: int) -> None:
    response = client.get(
        "/ohlc", params={"instrument": "XAU_USD", "granularity": "H4", "count": count}
    )

    assert response.status_code == 422


@respx.mock
def test_get_ohlc_forwards_valid_time_range_without_count(client: TestClient) -> None:
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={"instrument": "XAU_USD", "granularity": "H4", "candles": []},
        )
    )

    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": "2020-05-12T00:00:00Z",
            "to": "2020-08-12T23:59:59+07:00",
        },
    )

    assert response.status_code == 200
    params = route.calls.last.request.url.params
    assert "count" not in params
    assert params["from"] == "2020-05-12T00:00:00Z"
    assert params["to"] == "2020-08-12T16:59:59Z"


@respx.mock
def test_get_ohlc_omits_to_when_not_supplied(client: TestClient) -> None:
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={"instrument": "XAU_USD", "granularity": "H4", "candles": []},
        )
    )
    start = datetime.now(timezone.utc) - timedelta(days=1)

    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": start.isoformat(),
        },
    )

    assert response.status_code == 200
    params = route.calls.last.request.url.params
    assert params["from"] == start.isoformat().replace("+00:00", "Z")
    assert "to" not in params
    assert "count" not in params


def test_count_cannot_be_combined_with_from(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "count": 100,
            "from": "2020-05-12T00:00:00Z",
        },
    )

    assert response.status_code == 422


def test_to_requires_from(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "to": "2020-08-12T23:59:59Z",
        },
    )

    assert response.status_code == 422


def test_future_to_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "to": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422


def test_future_from_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422


def test_from_must_be_before_to(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": "2020-08-12T23:59:59Z",
            "to": "2020-05-12T00:00:00Z",
        },
    )

    assert response.status_code == 422


def test_range_timestamps_require_timezone(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": "2020-05-12T00:00:00",
        },
    )

    assert response.status_code == 422


def test_unknown_query_parameter_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/ohlc",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "counts": 100,
        },
    )

    assert response.status_code == 422


@respx.mock
def test_oanda_auth_error_does_not_leak_details(client: TestClient) -> None:
    route = respx.get(
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
    assert route.calls.last.request.url.params["count"] == "100"
