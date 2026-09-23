import csv
import json
from collections.abc import AsyncIterator
from io import StringIO

from app.models import Candle, HistoricalOhlcQuery
from app.oanda import OandaService


def _rfc3339(value) -> str:  # type: ignore[no-untyped-def]
    return value.isoformat().replace("+00:00", "Z")


def _candle_payload(candle: Candle) -> dict[str, object]:
    return {
        "time": _rfc3339(candle.time),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": candle.volume,
        "complete": candle.complete,
    }


async def historical_json_stream(
    service: OandaService,
    query: HistoricalOhlcQuery,
    first_chunk: list[Candle],
) -> AsyncIterator[str]:
    metadata = {
        "instrument": query.instrument,
        "granularity": query.granularity.value,
        "from": _rfc3339(query.from_time),
        "until": _rfc3339(query.until_time),
    }
    prefix = json.dumps(metadata, separators=(",", ":"))
    yield prefix[:-1] + ',"candles":['

    count = 0
    first = True
    async for candle in service.iter_historical_candles(query, first_chunk):
        if not first:
            yield ","
        yield json.dumps(_candle_payload(candle), separators=(",", ":"))
        first = False
        count += 1

    yield f'],"count":{count}}}'


async def historical_csv_stream(
    service: OandaService,
    query: HistoricalOhlcQuery,
    first_chunk: list[Candle],
) -> AsyncIterator[str]:
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "instrument",
            "granularity",
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "complete",
        )
    )
    yield stream.getvalue()

    async for candle in service.iter_historical_candles(query, first_chunk):
        stream.seek(0)
        stream.truncate(0)
        writer.writerow(
            (
                query.instrument,
                query.granularity.value,
                _rfc3339(candle.time),
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                candle.volume,
                "true" if candle.complete else "false",
            )
        )
        yield stream.getvalue()
