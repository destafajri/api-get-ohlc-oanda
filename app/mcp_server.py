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
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError
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
from app.mcp_auth import CompositeTokenVerifier, McpOAuthConfigurationError
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


def create_mcp_server(
    *,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
) -> MCPServer:
    server = MCPServer(
        "OANDA OHLC MCP",
        auth=auth,
        token_verifier=token_verifier,
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


def create_runtime_mcp_server(settings: McpSettings) -> MCPServer:
    """Create a fresh HTTP runtime server for one ASGI application lifespan."""

    try:
        oauth_values = _oauth_values(settings)
    except McpOAuthConfigurationError:
        # Keep the REST API available. build_mcp_http_app() will expose a
        # fail-closed 503 for MCP while still initializing the session manager.
        return create_mcp_server()

    if oauth_values is None:
        return create_mcp_server()

    public_url, issuer_url, jwks_url = oauth_values
    token_verifier = CompositeTokenVerifier(
        issuer_url=issuer_url,
        resource_url=public_url,
        jwks_url=jwks_url,
        static_token=settings.mcp_auth_token,
    )
    auth = AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=public_url,
        required_scopes=["openid"],
        validate_token_resource=True,
    )
    return create_mcp_server(auth=auth, token_verifier=token_verifier)


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
    if settings.mcp_public_url is not None and settings.mcp_public_url.host:
        hosts.add(settings.mcp_public_url.host)
    return sorted(hosts)


def _allowed_origins(settings: McpSettings) -> list[str]:
    origins = {
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        *(f"https://{host}" for host in _vercel_hosts()),
        *_csv_values(settings.mcp_allowed_origins),
    }
    if settings.mcp_public_url is not None and settings.mcp_public_url.host:
        origins.add(
            f"{settings.mcp_public_url.scheme}://{settings.mcp_public_url.host}"
        )
    return sorted(origins)


def _cors_origins(settings: McpSettings) -> list[str]:
    origins = {
        *(f"https://{host}" for host in _vercel_hosts()),
        *_csv_values(settings.mcp_allowed_origins),
    }
    if settings.mcp_public_url is not None and settings.mcp_public_url.host:
        origins.add(
            f"{settings.mcp_public_url.scheme}://{settings.mcp_public_url.host}"
        )
    return sorted(origins)


def _oauth_values(settings: McpSettings) -> tuple[AnyHttpUrl, AnyHttpUrl, AnyHttpUrl] | None:
    values = (
        settings.mcp_public_url,
        settings.mcp_oauth_issuer_url,
        settings.mcp_oauth_jwks_url,
    )
    configured = [value is not None for value in values]
    if any(configured) and not all(configured):
        raise McpOAuthConfigurationError(
            "MCP_PUBLIC_URL, MCP_OAUTH_ISSUER_URL, and MCP_OAUTH_JWKS_URL "
            "must be configured together."
        )
    if not any(configured):
        return None

    public_url, issuer_url, jwks_url = values
    assert public_url is not None
    assert issuer_url is not None
    assert jwks_url is not None

    if any(url.scheme != "https" for url in (public_url, issuer_url, jwks_url)):
        raise McpOAuthConfigurationError(
            "MCP OAuth URLs must use HTTPS."
        )
    if public_url.path.rstrip("/") != "/mcp" or public_url.query or public_url.fragment:
        raise McpOAuthConfigurationError(
            "MCP_PUBLIC_URL must be the canonical HTTPS /mcp/ endpoint "
            "without query parameters or a fragment."
        )
    return public_url, issuer_url, jwks_url


class McpBearerAuthMiddleware:
    """Static Bearer compatibility mode used when OAuth is not configured."""

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

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
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

        await self.app(scope, receive, send)


class NoStoreMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        async def send_no_store(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_no_store)


class MisconfiguredMcpApp:
    def __init__(self, app: ASGIApp, message: str) -> None:
        self.app = app
        self.message = message

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {
                "error": "mcp_auth_not_configured",
                "message": self.message,
            },
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def build_mcp_http_app(
    server: MCPServer,
    settings: McpSettings | None = None,
) -> ASGIApp:
    settings = settings or get_mcp_settings()
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(settings),
        allowed_origins=_allowed_origins(settings),
    )

    oauth_error: McpOAuthConfigurationError | None = None
    try:
        oauth_values = _oauth_values(settings)
    except McpOAuthConfigurationError as exc:
        oauth_values = None
        oauth_error = exc

    app: ASGIApp = server.streamable_http_app(
        streamable_http_path="/mcp/",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )

    if oauth_error is not None:
        app = MisconfiguredMcpApp(app, str(oauth_error))
    elif oauth_values is None:
        app = McpBearerAuthMiddleware(app)

    app = NoStoreMiddleware(app)

    cors_origins = _cors_origins(settings)
    if cors_origins:
        app = CORSMiddleware(
            app=app,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
        )
    return app


class McpMount:
    """Mutable ASGI fallback target rebuilt for each host-app lifespan."""

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
