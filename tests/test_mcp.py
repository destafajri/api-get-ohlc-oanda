from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from mcp import Client

from app.config import get_mcp_settings, get_settings
from app.mcp_server import build_mcp_http_app, create_mcp_server, mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def configured_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OANDA_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "mcp-test-token")
    get_settings.cache_clear()
    get_mcp_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_mcp_settings.cache_clear()


@pytest.mark.anyio
async def test_mcp_advertises_only_get_ohlc() -> None:
    async with Client(mcp_server) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools.tools] == ["get_ohlc"]
    tool = tools.tools[0]
    assert set(tool.input_schema["properties"]) == {
        "instrument",
        "granularity",
        "count",
        "start_time",
        "end_time",
    }
    assert tool.output_schema is not None


@pytest.mark.anyio
@respx.mock
async def test_get_ohlc_tool_reuses_oanda_service_and_returns_structured_data() -> None:
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

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_ohlc",
            {"instrument": "XAU_USD", "granularity": "H4", "count": 1},
        )

    assert result.is_error is False
    assert result.structured_content == {
        "source": "OANDA",
        "environment": "practice",
        "instrument": "XAU_USD",
        "granularity": "H4",
        "requested_count": 1,
        "start_time": None,
        "end_time": None,
        "returned_count": 1,
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
    assert route.calls.last.request.url.params["count"] == "1"


@pytest.mark.anyio
async def test_get_ohlc_tool_returns_actionable_validation_error() -> None:
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_ohlc",
            {
                "instrument": "XAU_USD",
                "granularity": "H4",
                "count": 100,
                "start_time": "2020-05-12T00:00:00Z",
            },
        )

    assert result.is_error is True
    assert "count cannot be combined with from or to" in result.content[0].text


def _initialize_payload() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }


def test_streamable_http_endpoint_initializes_with_bearer_auth() -> None:
    server = create_mcp_server()
    http_app = build_mcp_http_app(server)

    with TestClient(http_app, base_url="http://localhost") as client:
        response = client.post(
            "/",
            json=_initialize_payload(),
            headers={
                "Authorization": "Bearer mcp-test-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    assert payload["result"]["serverInfo"]["name"] == "OANDA OHLC MCP"
    assert response.headers["Cache-Control"] == "no-store"


def test_streamable_http_endpoint_rejects_unauthorized_requests() -> None:
    server = create_mcp_server()
    http_app = build_mcp_http_app(server)

    with TestClient(http_app, base_url="http://localhost") as client:
        response = client.post(
            "/",
            json=_initialize_payload(),
            headers={
                "Authorization": "Bearer wrong-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"error": "unauthorized"}


def test_streamable_http_endpoint_fails_closed_without_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    get_mcp_settings.cache_clear()
    server = create_mcp_server()
    http_app = build_mcp_http_app(server)

    with TestClient(http_app, base_url="http://localhost") as client:
        response = client.post(
            "/",
            json=_initialize_payload(),
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 503
    assert response.json() == {"error": "mcp_auth_not_configured"}
