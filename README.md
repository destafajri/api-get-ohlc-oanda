# OANDA OHLC API

A production-minimal FastAPI service that fetches midpoint candlesticks from OANDA and returns a small, stable JSON shape over HTTP GET.

## Features

- `GET /ohlc` with validated instrument, granularity, and count
- async upstream calls with connection pooling and timeouts
- normalized OHLC response; decimal prices remain strings to preserve precision
- safe upstream error mapping without leaking credentials or raw auth details
- `GET /health` liveness endpoint
- interactive OpenAPI docs at `/docs` and schema at `/openapi.json`
- environment-based OANDA practice/live configuration

## Requirements

- Python 3.11 or newer
- an OANDA v20 account and API token

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your own credentials:

```dotenv
OANDA_TOKEN=your-token
OANDA_ACCOUNT_ID=your-account-id
OANDA_ENVIRONMENT=practice
OANDA_TIMEOUT_SECONDS=10
```

`OANDA_ENVIRONMENT` accepts only `practice` or `live`. The account ID is kept in configuration for account-scoped extensions; the current candles endpoint itself is instrument-scoped. Neither credential is returned by the API or logged by the application.

Start the server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI.

## Usage

```bash
curl --get 'http://localhost:8000/ohlc' \
  --data-urlencode 'instrument=XAU_USD' \
  --data-urlencode 'granularity=H4' \
  --data-urlencode 'count=100'
```

Example response:

```json
{
  "instrument": "XAU_USD",
  "granularity": "H4",
  "count": 1,
  "candles": [
    {
      "time": "2026-08-12T20:00:00Z",
      "open": "3348.210",
      "high": "3361.540",
      "low": "3342.800",
      "close": "3357.190",
      "volume": 1821,
      "complete": true
    }
  ]
}
```

Health check:

```bash
curl 'http://localhost:8000/health'
```

## Supported query parameters

| Parameter | Rules | Example |
| --- | --- | --- |
| `instrument` | uppercase OANDA pair with `_`, 3-20 characters | `XAU_USD` |
| `granularity` | OANDA candle granularity from `S5` through `M` | `H4` |
| `count` | integer from 1 to 5000; defaults to 100 | `100` |

Only midpoint (`M`) candles are requested. Incomplete candles are retained and marked with `complete: false`, allowing callers to decide whether to use them.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock OANDA, so they do not require real credentials and never make trading API calls.

## Security notes

- Never commit `.env`; it is ignored by Git.
- Use a secret manager in production and inject the three `OANDA_*` variables at runtime.
- Put this API behind authentication or a private network before exposing it publicly. The OANDA token stays server-side, but an unprotected endpoint could still be abused to consume quota.
- Use HTTPS at the ingress or reverse proxy.
