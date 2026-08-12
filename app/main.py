from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app.config import ConfigurationError, Settings, get_settings
from app.models import (
    ErrorDetail,
    ErrorResponse,
    Granularity,
    HealthResponse,
    OhlcResponse,
)
from app.oanda import OandaService, OandaServiceError


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


@app.middleware("http")
async def add_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
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


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness check. This does not call OANDA or reveal configuration."""
    return HealthResponse()


@app.get(
    "/ohlc",
    response_model=OhlcResponse,
    responses={
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
    instrument: Annotated[
        str,
        Query(
            min_length=3,
            max_length=20,
            pattern=r"^[A-Z0-9]+_[A-Z0-9]+$",
            examples=["XAU_USD"],
        ),
    ],
    granularity: Annotated[Granularity, Query(examples=["H4"])],
    count: Annotated[int, Query(ge=1, le=5000)] = 100,
    settings: Settings = Depends(get_settings),
) -> OhlcResponse:
    """Return normalized midpoint OHLC candles from OANDA."""
    service = OandaService(request.app.state.http_client, settings)
    return await service.get_ohlc(instrument, granularity, count)
