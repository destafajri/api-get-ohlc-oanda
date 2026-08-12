from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class OhlcQuery(BaseModel):
    """Validated query modes for recent-count or explicit time-range requests."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    instrument: str = Field(
        min_length=3,
        max_length=20,
        pattern=r"^[A-Z0-9]+_[A-Z0-9]+$",
        examples=["XAU_USD"],
    )
    granularity: Granularity = Field(examples=["H4"])
    count: int | None = Field(
        default=None,
        ge=1,
        le=5000,
        description="Recent candles to return. Defaults to 100 outside range mode.",
    )
    from_time: datetime | None = Field(
        default=None,
        alias="from",
        description="Range start as an RFC3339 timestamp with timezone.",
        examples=["2026-05-12T00:00:00Z"],
    )
    to_time: datetime | None = Field(
        default=None,
        alias="to",
        description="Optional range end. OANDA uses the latest available data when omitted.",
        examples=["2026-08-12T23:59:59Z"],
    )

    @model_validator(mode="after")
    def validate_query_mode(self) -> Self:
        now = datetime.now(timezone.utc)

        if self.count is not None and (
            self.from_time is not None or self.to_time is not None
        ):
            raise ValueError("count cannot be combined with from or to")

        if self.from_time is None:
            if self.to_time is not None:
                raise ValueError("to requires from")
            self.count = 100 if self.count is None else self.count
            return self

        self._require_timezone(self.from_time, "from")
        if self.to_time is not None:
            self._require_timezone(self.to_time, "to")

        if self.from_time > now:
            raise ValueError("from cannot be later than the current time")
        if self.to_time is not None and self.to_time > now:
            raise ValueError("to cannot be later than the current time")
        if self.to_time is not None and self.from_time >= self.to_time:
            raise ValueError("from must be earlier than to")

        return self

    @staticmethod
    def _require_timezone(value: datetime, field_name: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{field_name} must include a timezone, for example Z or +07:00"
            )


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
