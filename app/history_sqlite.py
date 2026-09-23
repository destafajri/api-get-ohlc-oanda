from pathlib import Path
import sqlite3
import tempfile

from app.models import HistoricalOhlcQuery
from app.oanda import OandaService


_CREATE_CANDLES_TABLE = """
CREATE TABLE candles (
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    time TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
    PRIMARY KEY (instrument, timeframe, time)
) WITHOUT ROWID
"""


async def build_historical_sqlite(
    service: OandaService,
    query: HistoricalOhlcQuery,
    first_chunk,
) -> Path:
    """Build a temporary SQLite export and return its path."""
    temporary = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = Path(temporary.name)
    temporary.close()

    try:
        with sqlite3.connect(path) as connection:
            connection.execute(_CREATE_CANDLES_TABLE)

            rows: list[tuple[object, ...]] = []
            async for candle in service.iter_historical_candles(query, first_chunk):
                rows.append(
                    (
                        query.instrument,
                        query.granularity.value,
                        candle.time.isoformat().replace("+00:00", "Z"),
                        float(candle.open),
                        float(candle.high),
                        float(candle.low),
                        float(candle.close),
                        candle.volume,
                        int(candle.complete),
                    )
                )

                if len(rows) >= 1000:
                    connection.executemany(
                        """
                        INSERT INTO candles (
                            instrument, timeframe, time, open, high, low, close, volume, complete
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    rows.clear()

            if rows:
                connection.executemany(
                    """
                    INSERT INTO candles (
                        instrument, timeframe, time, open, high, low, close, volume, complete
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

            connection.commit()

        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
