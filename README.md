# OANDA OHLC API

A production-minimal FastAPI service that fetches midpoint candlesticks from OANDA and returns stable JSON or CSV over HTTP GET.

## Features

- `GET /ohlc` with validated recent-count and historical time-range modes
- async upstream calls with connection pooling and timeouts
- normalized OHLC response; decimal prices remain strings to preserve precision
- optional CSV output for AI tools, spreadsheets, and data pipelines
- safe upstream error mapping without leaking credentials or raw auth details
- `GET /health` liveness endpoint
- crawler-readable home page, `robots.txt`, and sitemap for URL discovery
- PNG and ICO favicon assets served with long-lived browser/CDN caching
- lightweight `HEAD` checks for `/`, `/health`, and `/ohlc`
- short browser/CDN caching for successful OHLC responses
- interactive OpenAPI docs at `/docs` and schema at `/openapi.json`
- environment-based OANDA practice/live configuration
- remote MCP endpoint over stateless Streamable HTTP with structured `get_ohlc` results
- fail-closed Bearer authentication for the remote MCP endpoint

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
MCP_AUTH_TOKEN=replace-with-a-long-random-token
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

## Usage: CSV output

JSON remains the default. Add `format=csv` to receive the same normalized
candles as `text/csv`:

```bash
curl --get 'http://localhost:8000/ohlc' \
  --data-urlencode 'instrument=XAU_USD' \
  --data-urlencode 'granularity=H4' \
  --data-urlencode 'count=100' \
  --data-urlencode 'format=csv'
```

Example CSV response:

```csv
instrument,granularity,time,open,high,low,close,volume,complete
XAU_USD,H4,2026-08-12T20:00:00Z,3348.210,3361.540,3342.800,3357.190,1821,true
```

CSV responses use `Content-Type: text/csv; charset=utf-8` and include an
inline filename such as `XAU_USD-H4.csv`. The `format` parameter works with
both recent-count and time-range requests.

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
| `format` | optional response format: `json` (default) or `csv` | `csv` |
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
- `format` must be either `json` or `csv`.
- unknown query parameters are rejected to catch typos.

Only midpoint (`M`) candles are requested. Incomplete candles are retained and marked with `complete: false`, allowing callers to decide whether to use them.

## Remote MCP server

The same OANDA implementation is exposed to AI clients through a remote MCP
server. The MCP layer calls the existing `OandaService`; it does not duplicate
the OANDA request or normalization logic.

### Endpoint and transport

- endpoint: `https://<deployment-host>/mcp/`
- local endpoint: `http://localhost:8000/mcp/`
- transport: MCP Streamable HTTP
- mode: stateless HTTP with JSON responses
- authentication: `Authorization: Bearer <MCP_AUTH_TOKEN>`
- tools: `get_ohlc`
- legacy HTTP+SSE transport is not exposed

The trailing slash is intentional because the MCP ASGI application is mounted
under `/mcp`. Use `/mcp/` in client configuration to avoid an HTTP redirect.

The endpoint fails closed with HTTP `503` when `MCP_AUTH_TOKEN` is not
configured. A wrong or missing Bearer token returns HTTP `401`.

### MCP environment variables

```dotenv
# Existing OANDA configuration
OANDA_TOKEN=your-oanda-token
OANDA_ENVIRONMENT=practice
OANDA_TIMEOUT_SECONDS=10

# Required for remote MCP access
MCP_AUTH_TOKEN=replace-with-a-long-random-token

# Optional comma-separated overrides
MCP_ALLOWED_HOSTS=
MCP_ALLOWED_ORIGINS=
```

`MCP_ALLOWED_HOSTS` contains hostnames without a scheme, for example
`ohlc.example.com`. `MCP_ALLOWED_ORIGINS` contains full origins, for example
`https://app.example.com`. They are only needed when the deployment hostname
or browser origin is not already covered by the local/Vercel defaults.

Do not reuse the OANDA API token as `MCP_AUTH_TOKEN`.

### `get_ohlc` tool

| Input | Type | Required | Rules |
| --- | --- | --- | --- |
| `instrument` | string | yes | uppercase OANDA instrument matching `^[A-Z0-9]+_[A-Z0-9]+$` |
| `granularity` | enum | yes | one of `S5,S10,S15,S30,M1,M2,M4,M5,M10,M15,M30,H1,H2,H3,H4,H6,H8,H12,D,W,M` |
| `count` | integer | no | 1-5000; defaults to 100 when no time range is supplied |
| `start_time` | RFC3339 datetime | no | explicit timezone required; cannot be combined with `count` |
| `end_time` | RFC3339 datetime | no | requires `start_time`; explicit timezone required |

The same range validation as the REST API applies: future timestamps are
rejected, `start_time` must be earlier than `end_time`, and count mode cannot
be mixed with range mode.

Tool results are structured data with:

- `source`: always `OANDA`
- `environment`: `practice` or `live`
- `instrument` and `granularity`
- `requested_count`, `start_time`, and `end_time`
- `returned_count`
- normalized candles with `time`, `open`, `high`, `low`, `close`,
  `volume`, and `complete`

Prices remain decimal strings to avoid floating-point precision loss. OANDA
credentials and the MCP access token are never included in tool results.

### Vercel deployment

The MCP server uses the existing FastAPI application in `app/main.py`; no
separate long-running process or persistent local state is required. No
`vercel.json` is required for this repository.

Configure the following Vercel environment variables before deploying:

```text
OANDA_TOKEN
OANDA_ENVIRONMENT
OANDA_TIMEOUT_SECONDS
MCP_AUTH_TOKEN
```

Vercel deployment hosts are accepted automatically when the corresponding
`VERCEL_*` runtime variables are present. For a custom domain, add the domain
explicitly, for example:

```text
MCP_ALLOWED_HOSTS=ohlc.example.com
```

Add `MCP_ALLOWED_ORIGINS` only for browser clients that must make cross-origin
requests directly to the MCP endpoint. Server-to-server MCP clients normally do
not require CORS configuration.

### Connect from ChatGPT

Current ChatGPT Desktop builds support Streamable HTTP MCP servers and share
their MCP configuration with Codex. The UI path is **Settings > MCP Servers >
Add Server**; choose Streamable HTTP and use the deployed `/mcp/` URL.

For a Bearer token supplied from an environment variable, the shared
`~/.codex/config.toml` form is:

```toml
[mcp_servers.oanda_ohlc]
url = "https://<deployment-host>/mcp/"
bearer_token_env_var = "OANDA_MCP_TOKEN"
```

Then set `OANDA_MCP_TOKEN` in the environment that launches the client.

ChatGPT Web does not read the local Codex configuration. Hosted ChatGPT MCP
tools are distributed through the plugin system, which is a separate packaging
and installation layer and is intentionally not added to this server repo.

### Connect from OpenAI Codex

Codex CLI and the Codex IDE extension support Streamable HTTP and Bearer-token
authentication:

```toml
# ~/.codex/config.toml or .codex/config.toml
[mcp_servers.oanda_ohlc]
url = "https://<deployment-host>/mcp/"
bearer_token_env_var = "OANDA_MCP_TOKEN"
```

Set the token before starting Codex:

```bash
export OANDA_MCP_TOKEN='replace-with-your-mcp-token'
codex
```

Use `/mcp` or `codex mcp list` to inspect the connection.

### Connect from Claude

The Claude API MCP connector can call a public Streamable HTTP endpoint and send
an authorization token. Configure the server entry with the deployed URL and
the same value as `MCP_AUTH_TOKEN`:

```python
mcp_servers=[
    {
        "type": "url",
        "name": "oanda-ohlc",
        "url": "https://<deployment-host>/mcp/",
        "authorization_token": os.environ["OANDA_MCP_TOKEN"],
    }
]
```

Claude web/Claude Desktop custom connectors can connect directly to remote MCP
servers, but their managed connector authentication flow is OAuth-oriented.
This repository intentionally implements a simpler static Bearer token instead
of an OAuth authorization server. If the Claude connector UI in your client
does not provide a way to send that static token, use Claude Code, the Claude
API MCP connector, or place a standards-compliant OAuth gateway in front of
`/mcp/`. Do not make the MCP endpoint public just to bypass authentication.

### Connect from Claude Code

```bash
export OANDA_MCP_TOKEN='replace-with-your-mcp-token'

claude mcp add --transport http oanda-ohlc \
  https://<deployment-host>/mcp/ \
  --header "Authorization: Bearer $OANDA_MCP_TOKEN"
```

Use `claude mcp list` or `/mcp` to verify the configured server.

### Connect from Cursor

Add this to project-level `.cursor/mcp.json` or the global MCP config:

```json
{
  "mcpServers": {
    "oanda-ohlc": {
      "url": "https://<deployment-host>/mcp/",
      "headers": {
        "Authorization": "Bearer ${env:OANDA_MCP_TOKEN}"
      }
    }
  }
}
```

Cursor detects remote HTTP endpoints and supports Streamable HTTP.

### Connect from Windsurf

Use a remote server entry with `serverUrl`:

```json
{
  "mcpServers": {
    "oanda-ohlc": {
      "serverUrl": "https://<deployment-host>/mcp/",
      "headers": {
        "Authorization": "Bearer <MCP_AUTH_TOKEN>"
      }
    }
  }
}
```

Keep the real token in the client's secret/environment mechanism when
available rather than committing it to a workspace file.

### Connect from Cline

Cline requires the Streamable HTTP transport name explicitly for remote
servers:

```json
{
  "mcpServers": {
    "oanda-ohlc": {
      "type": "streamableHttp",
      "url": "https://<deployment-host>/mcp/",
      "headers": {
        "Authorization": "Bearer <MCP_AUTH_TOKEN>"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

### Connect from Continue

Continue supports remote `streamable-http` MCP servers in `config.yaml`.
Keep the token in Continue's secret store or environment:

```yaml
mcpServers:
  - name: oanda-ohlc
    type: streamable-http
    url: https://<deployment-host>/mcp/
    requestOptions:
      headers:
        Authorization: "Bearer ${{ secrets.OANDA_MCP_TOKEN }}"
```

### Generic MCP client

For any other standards-compliant client, configure:

```text
transport: Streamable HTTP
url: https://<deployment-host>/mcp/
header: Authorization: Bearer <MCP_AUTH_TOKEN>
tool: get_ohlc
```

Configuration file names and secret interpolation syntax are client-specific;
the server protocol and endpoint are not.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The test suite covers the existing REST API plus MCP tool discovery, structured
tool results, validation errors, safe upstream error mapping, Streamable HTTP
initialization, FastAPI mounting, stateless behavior, Bearer authentication,
fail-closed configuration, and Origin rejection.

OANDA calls are mocked in automated tests, so the suite does not require or use
live OANDA credentials.

## Security notes

- Never commit `.env`; it is ignored by Git.
- Use a secret manager in production and inject `OANDA_TOKEN` and `MCP_AUTH_TOKEN` at runtime. Configure `OANDA_ENVIRONMENT` and `OANDA_TIMEOUT_SECONDS` as ordinary environment settings.
- The remote MCP endpoint is protected independently from the OANDA token and is never cacheable (`Cache-Control: no-store`).
- Put this API behind authentication or a private network before exposing it publicly. The OANDA token stays server-side, but an unprotected endpoint could still be abused to consume quota.
- Use HTTPS at the ingress or reverse proxy.
