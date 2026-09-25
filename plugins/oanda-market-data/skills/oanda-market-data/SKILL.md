---
name: oanda-market-data
description: Retrieve normalized OANDA OHLC candles for market analysis, quantitative research, and timeframe comparisons.
---

Use the `oanda_market_data` MCP server and its `get_ohlc` tool whenever the user asks for OANDA candlestick data or analysis that requires OANDA OHLC candles.

Rules:

- Use `list_instruments` when the user asks what instruments are available or when the exact OANDA symbol is unknown.
- Use OANDA instrument names such as `XAU_USD`.
- Use `count` for recent candles.
- Use `start_time` and optional `end_time` for historical ranges; do not combine them with `count`.
- Treat prices as decimal strings and preserve their precision.
- Distinguish complete from incomplete candles when the distinction matters.
- Do not claim the plugin can place, modify, or cancel trades. It is read-only market data.
- If required inputs are materially ambiguous, ask for the missing instrument, timeframe, or range rather than guessing.
