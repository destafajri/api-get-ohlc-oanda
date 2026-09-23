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


class HistoricalGranularity(StrEnum):
    M5 = "M5"
    M15 = "M15"
    H4 = "H4"


class OutputFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


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
    daily_alignment: int | None = Field(
        default=None,
        alias="dailyAlignment",
        ge=0,
        le=23,
        description="OANDA daily candle alignment hour.",
        examples=[17],
    )
    alignment_timezone: str | None = Field(
        default=None,
        alias="alignmentTimezone",
        min_length=1,
        max_length=100,
        description="OANDA candle alignment timezone.",
        examples=["America/New_York"],
    )
    smooth: bool | None = Field(
        default=None,
        description="Whether OANDA should smooth candle opens.",
        examples=[False],
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.JSON,
        alias="format",
        description="Response representation. JSON is the default.",
        examples=["csv"],
    )
    key: str | None = Field(
        default=None,
        min_length=1,
        description="API key for historical OHLC access.",
    )
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


class HistoricalOhlcQuery(BaseModel):
    """Validated query for paginated historical OHLC exports."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    instrument: str = Field(
        min_length=3,
        max_length=20,
        pattern=r"^[A-Z0-9]+_[A-Z0-9]+$",
        examples=["XAU_USD"],
    )
    granularity: HistoricalGranularity = Field(examples=["H4"])
    output_format: OutputFormat = Field(
        default=OutputFormat.JSON,
        alias="format",
        description="Response representation. JSON is the default.",
        examples=["csv"],
    )
    from_time: datetime = Field(
        default=datetime(2005, 1, 1, tzinfo=timezone.utc),
        alias="from",
        description="Inclusive start. Defaults to 2005-01-01T00:00:00Z.",
        examples=["2005-01-01T00:00:00Z"],
    )
    until_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        alias="until",
        description="Exclusive end. Defaults to the current request time.",
        examples=["2026-09-23T08:51:40Z"],
    )

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        now = datetime.now(timezone.utc)
        self._require_timezone(self.from_time, "from")
        self._require_timezone(self.until_time, "until")

        if self.from_time > now:
            raise ValueError("from cannot be later than the current time")
        if self.until_time > now:
            raise ValueError("until cannot be later than the current time")
        if self.from_time >= self.until_time:
            raise ValueError("from must be earlier than until")
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


class ResearchAccountContext(BaseModel):
    site_id: str
    division_id: str


class ResearchContextResponse(BaseModel):
    environment: str
    upstream: str
    accounts: list[ResearchAccountContext]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
