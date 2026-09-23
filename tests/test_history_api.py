from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def history_client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        oanda_token="test-token",
        oanda_environment="practice",
        historical_api_key="history-key",
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _candle(at: datetime, price: str = "2000.000") -> dict[str, object]:
    return {
        "complete": True,
        "volume": 10,
        "time": at.isoformat().replace("+00:00", "Z"),
        "mid": {"o": price, "h": price, "l": price, "c": price},
    }


def test_history_requires_api_key(history_client: TestClient) -> None:
    response = history_client.get(
        "/ohlc/history",
        params={"instrument": "XAU_USD", "granularity": "H4"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "historical_api_unauthorized"
    assert response.headers["Cache-Control"] == "no-store"


def test_history_fails_closed_when_api_key_is_not_configured() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        oanda_token="test-token",
        oanda_environment="practice",
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                "/ohlc/history",
                params={"instrument": "XAU_USD", "granularity": "H4", "key": "anything"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "historical_api_not_configured"


def test_history_chunk_delay_defaults_to_five_seconds() -> None:
    settings = Settings(oanda_token="test-token")
    assert settings.historical_chunk_delay_seconds == 5.0


def test_history_chunk_delay_can_be_configured() -> None:
    settings = Settings(
        oanda_token="test-token",
        historical_chunk_delay_seconds=10,
    )
    assert settings.historical_chunk_delay_seconds == 10.0


def test_history_page_size_defaults_to_2500() -> None:
    settings = Settings(oanda_token="test-token")
    assert settings.historical_page_size == 2500


def test_history_page_size_can_be_configured() -> None:
    settings = Settings(oanda_token="test-token", historical_page_size=1000)
    assert settings.historical_page_size == 1000


@respx.mock
def test_history_defaults_xau_usd_to_first_available_candle_and_now(history_client: TestClient) -> None:
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={"instrument": "XAU_USD", "granularity": "H4", "candles": []},
        )
    )
    before = datetime.now(timezone.utc)

    response = history_client.get(
        "/ohlc/history",
        params={"instrument": "XAU_USD", "granularity": "H4", "key": "history-key"},
    )
    after = datetime.now(timezone.utc)

    assert response.status_code == 200
    payload = response.json()
    assert payload["from"] == "2006-03-19T22:00:00Z"
    until = datetime.fromisoformat(payload["until"].replace("Z", "+00:00"))
    assert before <= until <= after
    assert payload["count"] == 0
    params = route.calls.last.request.url.params
    assert params["from"] == "2006-03-19T22:00:00Z"
    assert params["count"] == "2500"
    assert params["includeFirst"] == "true"


@respx.mock
def test_history_continues_after_full_include_first_false_page(
    history_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    first = [_candle(start + timedelta(hours=4 * index)) for index in range(2500)]
    first_last = start + timedelta(hours=4 * 2499)

    second = [
        _candle(first_last + timedelta(hours=4 * (index + 1)))
        for index in range(2499)
    ]
    second_last = first_last + timedelta(hours=4 * 2499)
    third_time = second_last + timedelta(hours=4)

    responses = [
        Response(
            200,
            json={"instrument": "XAU_USD", "granularity": "H4", "candles": first},
        ),
        Response(
            200,
            json={"instrument": "XAU_USD", "granularity": "H4", "candles": second},
        ),
        Response(
            200,
            json={
                "instrument": "XAU_USD",
                "granularity": "H4",
                "candles": [_candle(third_time)],
            },
        ),
    ]

    def handler(_request):  # type: ignore[no-untyped-def]
        return responses.pop(0)

    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(side_effect=handler)
    sleep = AsyncMock()
    monkeypatch.setattr("app.oanda.asyncio.sleep", sleep)

    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": start.isoformat(),
            "until": (third_time + timedelta(hours=4)).isoformat(),
            "key": "history-key",
        },
    )

    assert response.status_code == 200
    assert response.json()["count"] == 5000
    assert route.call_count == 3

    second_params = route.calls[1].request.url.params
    assert second_params["from"] == first_last.isoformat().replace("+00:00", "Z")
    assert second_params["count"] == "2500"
    assert second_params["includeFirst"] == "false"

    third_params = route.calls[2].request.url.params
    assert third_params["from"] == second_last.isoformat().replace("+00:00", "Z")
    assert third_params["count"] == "2500"
    assert third_params["includeFirst"] == "false"

    assert sleep.await_count == 2
    sleep.assert_awaited_with(5.0)


@respx.mock
def test_history_retries_transient_oanda_gateway_timeout(
    history_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    responses = [
        Response(504, text="Gateway Timeout"),
        Response(
            200,
            json={
                "instrument": "XAU_USD",
                "granularity": "H4",
                "candles": [_candle(at)],
            },
        ),
    ]

    def handler(_request):  # type: ignore[no-untyped-def]
        return responses.pop(0)

    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(side_effect=handler)
    sleep = AsyncMock()
    monkeypatch.setattr("app.oanda.asyncio.sleep", sleep)

    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": at.isoformat(),
            "until": (at + timedelta(hours=8)).isoformat(),
            "key": "history-key",
        },
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert route.call_count == 2
    sleep.assert_awaited_once_with(5.0)


@respx.mock
def test_history_preserves_oanda_422_error_message(
    history_client: TestClient,
) -> None:
    respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            422,
            json={"errorMessage": "Invalid value specified for 'from'"},
        )
    )

    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": "2005-01-01T00:00:00Z",
            "until": "2006-01-01T00:00:00Z",
            "key": "history-key",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid_oanda_request",
        "message": "Invalid value specified for 'from'",
    }


@respx.mock
def test_history_until_is_exclusive_and_stops_pagination(
    history_client: TestClient,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until = start + timedelta(hours=8)
    route = respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={
                "instrument": "XAU_USD",
                "granularity": "H4",
                "candles": [
                    _candle(start),
                    _candle(start + timedelta(hours=4)),
                    _candle(until),
                ],
            },
        )
    )

    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": start.isoformat(),
            "until": until.isoformat(),
            "key": "history-key",
        },
    )

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert route.call_count == 1


@respx.mock
def test_history_can_stream_csv(history_client: TestClient) -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={
                "instrument": "XAU_USD",
                "granularity": "M15",
                "candles": [_candle(at, "4321.123")],
            },
        )
    )

    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "M15",
            "from": at.isoformat(),
            "until": (at + timedelta(minutes=30)).isoformat(),
            "format": "csv",
            "key": "history-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="XAU_USD-M15-history.csv"'
    )
    assert response.text.startswith(
        "instrument,granularity,time,open,high,low,close,volume,complete\n"
    )
    assert "XAU_USD,M15,2026-01-01T00:00:00Z,4321.123" in response.text
    assert response.headers["Cache-Control"] == "no-store"


@respx.mock
def test_history_can_export_sqlite(history_client: TestClient) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    respx.get(
        "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    ).mock(
        return_value=Response(
            200,
            json={
                "instrument": "XAU_USD",
                "granularity": "H4",
                "candles": [
                    _candle(start, "4321.125"),
                    _candle(start + timedelta(hours=4), "4322.250"),
                ],
            },
        )
    )

    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": start.isoformat(),
            "until": (start + timedelta(hours=8)).isoformat(),
            "format": "sqlite",
            "key": "history-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.sqlite3")
    assert response.headers["content-disposition"] == (
        'attachment; filename="XAU_USD-H4-history.db"'
    )

    with tempfile.NamedTemporaryFile(suffix=".db") as database_file:
        database_file.write(response.content)
        database_file.flush()
        with sqlite3.connect(database_file.name) as connection:
            columns = [
                row[1]
                for row in connection.execute("PRAGMA table_info(candles)").fetchall()
            ]
            rows = connection.execute(
                """
                SELECT instrument, timeframe, time, open, high, low, close, volume, complete
                FROM candles
                ORDER BY time
                """
            ).fetchall()
            indexes = connection.execute(
                "PRAGMA index_list(candles)"
            ).fetchall()

    assert columns == [
        "instrument",
        "timeframe",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "complete",
    ]
    assert rows == [
        (
            "XAU_USD",
            "H4",
            "2026-01-01T00:00:00Z",
            4321.125,
            4321.125,
            4321.125,
            4321.125,
            10,
            1,
        ),
        (
            "XAU_USD",
            "H4",
            "2026-01-01T04:00:00Z",
            4322.25,
            4322.25,
            4322.25,
            4322.25,
            10,
            1,
        ),
    ]
    assert any(index[2] == 1 for index in indexes)


@pytest.mark.parametrize("granularity", ["M1", "D", "S5"])
def test_history_rejects_granularities_outside_m5_m15_h4(
    history_client: TestClient, granularity: str
) -> None:
    response = history_client.get(
        "/ohlc/history",
        params={"instrument": "XAU_USD", "granularity": granularity, "key": "history-key"},
    )

    assert response.status_code == 422


def test_history_rejects_invalid_time_range(history_client: TestClient) -> None:
    response = history_client.get(
        "/ohlc/history",
        params={
            "instrument": "XAU_USD",
            "granularity": "H4",
            "from": "2026-01-02T00:00:00Z",
            "until": "2026-01-01T00:00:00Z",
            "key": "history-key",
        },
    )

    assert response.status_code == 422
