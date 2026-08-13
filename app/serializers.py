import csv
from io import StringIO

from app.models import OhlcResponse


CSV_COLUMNS = (
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


def ohlc_to_csv(result: OhlcResponse) -> str:
    """Serialize normalized OHLC data without losing decimal precision."""
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)

    for candle in result.candles:
        timestamp = candle.time.isoformat().replace("+00:00", "Z")
        writer.writerow(
            (
                result.instrument,
                result.granularity.value,
                timestamp,
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                candle.volume,
                "true" if candle.complete else "false",
            )
        )

    return stream.getvalue()
