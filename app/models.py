from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class Granularity(StrEnum):
    S5 = "S5"
    S10 = "S10"
    S15 = "S15"
    S30 = "S30"
    M1 = "M1"
    M2 = "M2"
    M4 = "M4"
    M5 = "M5"
    M10 = "M10"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"
    H4 = "H4"
    H6 = "H6"
    H8 = "H8"
    H12 = "H12"
    D = "D"
    W = "W"
    M = "M"


class Candle(BaseModel):
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = Field(ge=0)
    complete: bool


class OhlcResponse(BaseModel):
    instrument: str
    granularity: Granularity
    count: int = Field(ge=0)
    candles: list[Candle]


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
