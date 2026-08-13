# OANDA OHLC API

A production-minimal FastAPI service that fetches midpoint candlesticks from OANDA and returns a small, stable JSON shape over HTTP GET.

## Features

- `GET /ohlc` with validated recent-count and historical time-range modes
- async upstream calls with connection pooling and timeouts
- normalized OHLC response; decimal prices remain strings to preserve precision
- safe upstream error mapping without leaking credentials or raw auth details
- `GET /health` liveness endpoint
- crawler-readable home page, `robots.txt`, and sitemap for URL discovery
- PNG and ICO favicon assets served with long-lived browser/CDN caching
- lightweight `HEAD` checks for `/`, `/health`, and `/ohlc`
- short browser/CDN caching for successful OHLC responses
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
OANDA_ENVIRONMENT=practice
OANDA_TIMEOUT_SECONDS=10
```

`OANDA_ENVIRONMENT` accepts only `practice` or `live`. This instrument candles endpoint does not need an OANDA account ID. The bearer token is never returned by the API or logged by the application.

Start the server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI.

## Usage: latest candles

```bash
curl --get 'http://localhost:8000/ohlc' \
  --data-urlencode 'instrument=XAU_USD' \
  --data-urlencode 'granularity=H4' \
  --data-urlencode 'count=100'
```

If neither `count` nor `from` is supplied, `count` defaults to `100`.

## Usage: time range

Use RFC3339 timestamps with an explicit timezone (`Z` or an offset such as `+07:00`):

```bash
curl --get 'http://localhost:8000/ohlc' \
  --data-urlencode 'instrument=XAU_USD' \
  --data-urlencode 'granularity=H4' \
  --data-urlencode 'from=2026-05-12T00:00:00Z' \
  --data-urlencode 'to=2026-08-12T23:59:59Z'
```

`to` may be omitted. The API forwards only `from`, and OANDA returns candles through the latest available data:

```bash
curl --get 'http://localhost:8000/ohlc' \
  --data-urlencode 'instrument=XAU_USD' \
  --data-urlencode 'granularity=H4' \
  --data-urlencode 'from=2026-05-12T00:00:00Z'
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

URL preflight checks can use `HEAD` without contacting OANDA or requiring the
OANDA token to be loaded:

```bash
curl --head 'http://localhost:8000/ohlc?instrument=XAU_USD&granularity=H4&count=100'
```

The application returns a crawler-readable landing page at `/`, allows public
crawling through `/robots.txt`, and publishes `/sitemap.xml`. Successful OHLC
responses are cached in browsers for 30 seconds and at the CDN edge for 60
seconds. Query parameters are part of the cache key, so different instruments,
granularities, counts, and time ranges do not share a cached response.

## Supported query parameters

| Parameter | Rules | Example |
| --- | --- | --- |
| `instrument` | uppercase OANDA pair with `_`, 3-20 characters | `XAU_USD` |
| `granularity` | OANDA candle granularity from `S5` through `M` | `H4` |
| `count` | integer from 1 to 5000; defaults to 100 in latest-candles mode | `100` |
| `from` | optional RFC3339 range start with timezone; enables range mode | `2026-05-12T00:00:00Z` |
| `to` | optional RFC3339 range end with timezone; requires `from`; omitted means latest available data | `2026-08-12T23:59:59Z` |

### Request validation

Invalid requests are rejected locally with HTTP `422` before any request is sent to OANDA:

- `count` cannot be combined with either `from` or `to`.
- `to` cannot be supplied without `from`.
- `from` and `to` must include a timezone.
- neither timestamp may be later than the server's current time.
- `from` must be earlier than `to`.
- `count` must be between 1 and 5000.
- unknown query parameters are rejected to catch typos.

Only midpoint (`M`) candles are requested. Incomplete candles are retained and marked with `complete: false`, allowing callers to decide whether to use them.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests mock OANDA, so they do not require real credentials and never make trading API calls.

## Security notes

- Never commit `.env`; it is ignored by Git.
- Use a secret manager in production and inject `OANDA_TOKEN` at runtime. Configure `OANDA_ENVIRONMENT` and `OANDA_TIMEOUT_SECONDS` as ordinary environment settings.
- Put this API behind authentication or a private network before exposing it publicly. The OANDA token stays server-side, but an unprotected endpoint could still be abused to consume quota.
- Use HTTPS at the ingress or reverse proxy.
