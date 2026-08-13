from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Annotated, AsyncIterator
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from app.config import ConfigurationError, Settings, get_settings
from app.models import (
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    OhlcQuery,
    OhlcResponse,
    OutputFormat,
)
from app.oanda import OandaService, OandaServiceError
from app.serializers import ohlc_to_csv


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(
    title="OANDA OHLC API",
    summary="A small, normalized HTTP API for OANDA candlestick data.",
    version="1.0.0",
    lifespan=lifespan,
)

OHLC_BROWSER_CACHE = "public, max-age=30, stale-while-revalidate=30"
OHLC_CDN_CACHE = (
    "public, max-age=60, stale-while-revalidate=300, stale-if-error=86400"
)
DISCOVERY_BROWSER_CACHE = "public, max-age=300"
DISCOVERY_CDN_CACHE = "public, max-age=86400, stale-while-revalidate=604800"
FAVICON_BROWSER_CACHE = "public, max-age=604800, immutable"
FAVICON_CDN_CACHE = "public, max-age=31536000, immutable"
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.middleware("http")
async def add_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    if response.status_code == 200 and request.url.path == "/ohlc":
        response.headers["Cache-Control"] = OHLC_BROWSER_CACHE
        response.headers["CDN-Cache-Control"] = OHLC_CDN_CACHE
        response.headers["Vercel-CDN-Cache-Control"] = OHLC_CDN_CACHE
    elif response.status_code == 200 and request.url.path in {
        "/",
        "/robots.txt",
        "/sitemap.xml",
    }:
        response.headers["Cache-Control"] = DISCOVERY_BROWSER_CACHE
        response.headers["CDN-Cache-Control"] = DISCOVERY_CDN_CACHE
        response.headers["Vercel-CDN-Cache-Control"] = DISCOVERY_CDN_CACHE
    elif response.status_code == 200 and request.url.path in {
        "/favicon.ico",
        "/favicon.png",
    }:
        response.headers["Cache-Control"] = FAVICON_BROWSER_CACHE
        response.headers["CDN-Cache-Control"] = FAVICON_CDN_CACHE
        response.headers["Vercel-CDN-Cache-Control"] = FAVICON_CDN_CACHE

    return response


@app.exception_handler(OandaServiceError)
async def oanda_error_handler(
    _request: Request, exc: OandaServiceError
) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=exc.code, message=exc.message))
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(
    _request: Request, _exc: ConfigurationError
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code="service_not_configured",
            message="Required OANDA configuration is missing or invalid.",
        )
    )
    return JSONResponse(status_code=503, content=body.model_dump())


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request) -> HTMLResponse:
    """Human- and crawler-readable entry point for the public API."""
    base_url = str(request.base_url).rstrip("/")
    canonical_url = escape(f"{base_url}/", quote=True)
    example_url = escape(
        f"{base_url}/ohlc?instrument=XAU_USD&granularity=H4&count=100",
        quote=True,
    )
    return HTMLResponse(
        content=f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="index,follow">
    <link rel="canonical" href="{canonical_url}">
    <link rel="icon" type="image/png" href="/favicon.png">
    <link rel="icon" href="/favicon.ico" sizes="any">
    <title>OANDA OHLC API</title>
    <meta name="description" content="Public HTTP GET API for normalized OANDA OHLC candlestick data.">
  </head>
  <body>
    <main>
      <h1>OANDA OHLC API</h1>
      <p>Fetch normalized midpoint candlesticks from OANDA over HTTP GET.</p>
      <ul>
        <li><a href="{example_url}">Example: XAU_USD H4, latest 100 candles</a></li>
        <li><a href="/docs">Interactive API documentation</a></li>
        <li><a href="/openapi.json">OpenAPI schema</a></li>
        <li><a href="/health">Health check</a></li>
      </ul>
    </main>
  </body>
</html>"""
    )


@app.head("/", include_in_schema=False)
async def head_root() -> Response:
    return Response(status_code=200, media_type="text/html")


@app.api_route(
    "/favicon.png",
    methods=["GET", "HEAD"],
    response_class=FileResponse,
    include_in_schema=False,
)
async def favicon_png() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.png", media_type="image/png")


@app.api_route(
    "/favicon.ico",
    methods=["GET", "HEAD"],
    response_class=FileResponse,
    include_in_schema=False,
)
async def favicon_ico() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots(request: Request) -> PlainTextResponse:
    sitemap_url = request.url_for("sitemap")
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}\n"
    )


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request) -> Response:
    base_url = str(request.base_url).rstrip("/")
    urls = [
        f"{base_url}/",
        f"{base_url}/docs",
        f"{base_url}/openapi.json",
        f"{base_url}/ohlc?instrument=XAU_USD&granularity=H4&count=100",
    ]
    entries = "".join(
        f"<url><loc>{escape(url)}</loc></url>" for url in urls
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness check. This does not call OANDA or reveal configuration."""
    return HealthResponse()


@app.head("/health", include_in_schema=False)
async def head_health() -> Response:
    return Response(status_code=200, media_type="application/json")


@app.head("/ohlc", include_in_schema=False)
async def head_ohlc(query: Annotated[OhlcQuery, Query()]) -> Response:
    """Validate an OHLC URL without calling OANDA or requiring credentials."""
    media_type = (
        "text/csv" if query.output_format is OutputFormat.CSV else "application/json"
    )
    return Response(status_code=200, media_type=media_type)


@app.get(
    "/ohlc",
    response_model=OhlcResponse,
    responses={
        200: {
            "description": "Normalized OHLC data as JSON or CSV.",
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "instrument,granularity,time,open,high,low,close,volume,complete\n"
                        "XAU_USD,H4,2026-08-12T20:00:00Z,3348.210,3361.540,"
                        "3342.800,3357.190,1821,true\n"
                    ),
                }
            },
        },
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
    tags=["market data"],
)
async def get_ohlc(
    request: Request,
    query: Annotated[OhlcQuery, Query()],
    settings: Settings = Depends(get_settings),
) -> OhlcResponse | Response:
    """Return normalized midpoint OHLC candles from OANDA."""
    service = OandaService(request.app.state.http_client, settings)
    result = await service.get_ohlc(query)
    if query.output_format is OutputFormat.CSV:
        filename = f"{result.instrument}-{result.granularity.value}.csv"
        return Response(
            content=ohlc_to_csv(result),
            media_type="text/csv",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    return result
