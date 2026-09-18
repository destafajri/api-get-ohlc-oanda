from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from hmac import compare_digest
from typing import Annotated, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import (
    ConfigurationError,
    McpSettings,
    get_mcp_settings,
    get_settings,
)
from app.http_client import create_http_client
from app.models import Candle, Granularity, OhlcQuery
from app.oanda import OandaService, OandaServiceError


@dataclass
class McpContext:
    http_client: httpx.AsyncClient


class OhlcToolResult(BaseModel):
    source: Literal["OANDA"] = "OANDA"
    environment: Literal["practice", "live"]
    instrument: str
    granularity: Granularity
    requested_count: int | None
    start_time: datetime | None
    end_time: datetime | None
    returned_count: int
    candles: list[Candle]


@asynccontextmanager
async def mcp_lifespan(_server: MCPServer) -> AsyncIterator[McpContext]:
    client = create_http_client()
    try:
        yield McpContext(http_client=client)
    finally:
        await client.aclose()


def _validation_message(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages)


def create_mcp_server() -> MCPServer:
    server = MCPServer(
        "OANDA OHLC MCP",
        instructions=(
            "Use get_ohlc to retrieve normalized midpoint candlestick data from OANDA. "
            "Use count for recent candles, or start_time/end_time for historical ranges."
        ),
        lifespan=mcp_lifespan,
    )

    @server.tool()
    async def get_ohlc(
        ctx: Context[McpContext],
        instrument: Annotated[
            str,
            Field(
                min_length=3,
                max_length=20,
                pattern=r"^[A-Z0-9]+_[A-Z0-9]+$",
                description="Uppercase OANDA instrument, for example XAU_USD.",
            ),
        ],
        granularity: Annotated[
            Granularity,
            Field(description="OANDA candle granularity, for example H4 or M15."),
        ],
        count: Annotated[
            int | None,
            Field(
                ge=1,
                le=5000,
                description=(
                    "Recent candle count. Omit when start_time or end_time is used. "
                    "Defaults to 100 when no time range is supplied."
                ),
            ),
        ] = None,
        start_time: Annotated[
            datetime | None,
            Field(
                description=(
                    "Historical range start as RFC3339 with an explicit timezone. "
                    "Cannot be combined with count."
                )
            ),
        ] = None,
        end_time: Annotated[
            datetime | None,
            Field(
                description=(
                    "Optional historical range end as RFC3339 with an explicit timezone. "
                    "Requires start_time."
                )
            ),
        ] = None,
    ) -> OhlcToolResult:
        """Retrieve normalized OANDA midpoint OHLC candles."""

        try:
            query = OhlcQuery(
                instrument=instrument,
                granularity=granularity,
                count=count,
                from_time=start_time,
                to_time=end_time,
            )
        except ValidationError as exc:
            raise ToolError(f"Invalid OHLC request: {_validation_message(exc)}") from exc

        try:
            settings = get_settings()
        except ConfigurationError as exc:
            raise ToolError(
                "OANDA configuration is missing or invalid on the MCP server."
            ) from exc

        service = OandaService(ctx.request_context.lifespan_context.http_client, settings)
        try:
            result = await service.get_ohlc(query)
        except OandaServiceError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc

        return OhlcToolResult(
            environment=settings.oanda_environment,
            instrument=result.instrument,
            granularity=result.granularity,
            requested_count=query.count if query.from_time is None else None,
            start_time=query.from_time,
            end_time=query.to_time,
            returned_count=result.count,
            candles=result.candles,
        )

    return server


def _csv_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _vercel_hosts() -> set[str]:
    return {
        value
        for name in (
            "VERCEL_URL",
            "VERCEL_BRANCH_URL",
            "VERCEL_PROJECT_PRODUCTION_URL",
        )
        if (value := os.getenv(name))
    }


def _allowed_hosts(settings: McpSettings) -> list[str]:
    hosts = {
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        *_vercel_hosts(),
        *_csv_values(settings.mcp_allowed_hosts),
    }
    return sorted(hosts)


def _allowed_origins(settings: McpSettings) -> list[str]:
    origins = {
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        *(f"https://{host}" for host in _vercel_hosts()),
        *_csv_values(settings.mcp_allowed_origins),
    }
    return sorted(origins)


def _cors_origins(settings: McpSettings) -> list[str]:
    origins = {
        *(f"https://{host}" for host in _vercel_hosts()),
        *_csv_values(settings.mcp_allowed_origins),
    }
    return sorted(origins)


class McpBearerAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        settings = get_mcp_settings()
        configured_token = settings.mcp_auth_token
        token_value = (
            configured_token.get_secret_value() if configured_token is not None else ""
        )
        if not token_value:
            response = JSONResponse(
                {"error": "mcp_auth_not_configured"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        supplied = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {token_value}"

        if not compare_digest(supplied, expected):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={
                    "WWW-Authenticate": "Bearer",
                    "Cache-Control": "no-store",
                },
            )
            await response(scope, receive, send)
            return

        async def send_no_store(message: dict) -> None:
            if message["type"] == "http.response.start":
                mutable_headers = list(message.get("headers", []))
                mutable_headers.append((b"cache-control", b"no-store"))
                message["headers"] = mutable_headers
            await send(message)

        await self.app(scope, receive, send_no_store)


def build_mcp_http_app(server: MCPServer) -> ASGIApp:
    settings = get_mcp_settings()
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(settings),
        allowed_origins=_allowed_origins(settings),
    )
    app: ASGIApp = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )
    app = McpBearerAuthMiddleware(app)

    cors_origins = _cors_origins(settings)
    if cors_origins:
        app = CORSMiddleware(
            app=app,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
    return app


class McpMount:
    """Mutable ASGI mount target rebuilt for each host-app lifespan."""

    def __init__(self) -> None:
        self._app: ASGIApp | None = None

    def set_app(self, app: ASGIApp | None) -> None:
        self._app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if self._app is None:
            response = JSONResponse(
                {"error": "mcp_not_ready"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


mcp_server = create_mcp_server()
mcp_mount = McpMount()
