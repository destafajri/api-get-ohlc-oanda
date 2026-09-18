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

### Endpoint, transport, and authentication

- endpoint: `https://<deployment-host>/mcp/`
- local endpoint: `http://localhost:8000/mcp/`
- transport: MCP Streamable HTTP
- mode: stateless HTTP with JSON responses
- tools: `get_ohlc`
- OAuth discovery: `/.well-known/oauth-protected-resource/mcp/`
- legacy HTTP+SSE transport is not exposed

The trailing slash on `/mcp/` is intentional and is part of the OAuth resource
identifier.

Two authentication modes are supported:

1. **OAuth 2.1 resource-server mode** for hosted clients such as ChatGPT. The
   MCP server publishes Protected Resource Metadata and validates RS256 JWT
   access tokens against the configured issuer, JWKS, exact resource audience,
   expiry, and the required `openid` scope.
2. **Static Bearer compatibility mode** for clients such as Codex and Claude
   Code. When `MCP_AUTH_TOKEN` is configured, that token remains accepted even
   while OAuth mode is enabled.

The MCP server does not issue OAuth tokens or store OAuth sessions. Token
issuance, user login, PKCE, client registration/CIMD, refresh tokens, and
consent are delegated to a standards-compatible OAuth authorization server.
This keeps the Vercel deployment stateless.

If any OAuth variable is configured, all OAuth variables must be configured
together. Partial OAuth configuration fails closed with HTTP `503`. With
OAuth disabled, `MCP_AUTH_TOKEN` is required and missing/empty configuration
also fails closed.

### MCP environment variables

```dotenv
# Existing OANDA configuration
OANDA_TOKEN=your-oanda-token
OANDA_ENVIRONMENT=practice
OANDA_TIMEOUT_SECONDS=10

# Optional static Bearer access. Required when OAuth is disabled.
MCP_AUTH_TOKEN=replace-with-a-long-random-token

# OAuth resource-server mode: configure all three together.
MCP_PUBLIC_URL=https://<deployment-host>/mcp/
MCP_OAUTH_ISSUER_URL=https://<your-oauth-issuer>
MCP_OAUTH_JWKS_URL=https://<your-oauth-issuer>/oauth2/jwks

# Optional comma-separated overrides
MCP_ALLOWED_HOSTS=
MCP_ALLOWED_ORIGINS=
```

`MCP_PUBLIC_URL` must exactly match the public MCP endpoint, including the
`/mcp/` path. Configure that exact value as the OAuth authorization server's
Resource Indicator/audience.

`MCP_ALLOWED_HOSTS` contains hostnames without a scheme, for example
`ohlc.example.com`. `MCP_ALLOWED_ORIGINS` contains full origins, for example
`https://app.example.com`. Vercel deployment hostnames are discovered from
the `VERCEL_*` runtime variables.

Never reuse the OANDA API token as `MCP_AUTH_TOKEN`.

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

The MCP server runs inside the existing FastAPI application in `app/main.py`.
It does not require a long-running process, sticky sessions, local persistent
state, or a separate `vercel.json`.

For static-Bearer-only access, configure:

```text
OANDA_TOKEN
OANDA_ENVIRONMENT
OANDA_TIMEOUT_SECONDS
MCP_AUTH_TOKEN
```

For OAuth access, also configure:

```text
MCP_PUBLIC_URL=https://<deployment-host>/mcp/
MCP_OAUTH_ISSUER_URL=https://<oauth-issuer>
MCP_OAUTH_JWKS_URL=https://<oauth-issuer>/oauth2/jwks
```

A custom domain may also need:

```text
MCP_ALLOWED_HOSTS=ohlc.example.com
```

Add `MCP_ALLOWED_ORIGINS` only when a browser client must make direct
cross-origin requests. Server-to-server MCP clients normally do not need CORS.

After changing Vercel environment variables, deploy again so the new runtime
receives them.

### Connect from ChatGPT

ChatGPT's OAuth connection requires a standards-compatible authorization
server in addition to this MCP resource server. The implementation is provider
agnostic. A practical example is WorkOS AuthKit because it provides MCP OAuth
authorization-server metadata, PKCE, Client ID Metadata Document (CIMD),
Dynamic Client Registration (DCR) compatibility, Resource Indicators, and a
JWKS endpoint.

Example production setup with AuthKit:

1. Create/enable AuthKit in a WorkOS project.
2. In **Connect > Configuration**, enable **Client ID Metadata Document
   (CIMD)**. Enable DCR too only if you want compatibility with clients that
   still use it.
3. Add the exact MCP endpoint as a **Resource Indicator**:
   `https://<deployment-host>/mcp/`. Setting it as the default Resource
   Indicator improves compatibility with clients that omit the OAuth
   `resource` parameter.
4. Set these Vercel variables using your AuthKit domain:

   ```text
   MCP_PUBLIC_URL=https://<deployment-host>/mcp/
   MCP_OAUTH_ISSUER_URL=https://<project>.authkit.app
   MCP_OAUTH_JWKS_URL=https://<project>.authkit.app/oauth2/jwks
   ```

5. Redeploy the Vercel project.
6. Verify discovery:

   ```bash
   curl https://<deployment-host>/.well-known/oauth-protected-resource/mcp/
   ```

   The response should identify the MCP resource and the configured OAuth
   authorization server.

7. In ChatGPT's **New Plugin** form use:

   ```text
   Name: OHLC
   Server URL: https://<deployment-host>/mcp/
   Authentication: OAuth
   ```

ChatGPT can then discover the authorization server, complete OAuth authorization
with PKCE, receive an access token, and send it to `/mcp/` as a Bearer token.
The MCP server verifies the token before advertising or executing
`get_ohlc`.

Do not switch the MCP endpoint to unauthenticated mode merely to make ChatGPT
connect.

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

The Claude API MCP connector can use the static Bearer token path directly:

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

Hosted Claude clients that use OAuth can use the same standards-based OAuth
resource-server mode described above, provided the client and selected
authorization server support the required MCP OAuth discovery/registration
flow.

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
initialization, FastAPI mounting, stateless behavior, static Bearer
authentication, OAuth Protected Resource Metadata, OAuth challenges, mocked
JWKS/JWT verification, audience rejection, partial-OAuth fail-closed behavior,
and Origin rejection.

OANDA calls are mocked in automated tests, so the suite does not require or use
live OANDA credentials.

## Security notes

- Never commit `.env`; it is ignored by Git.
- Use Vercel's secret/environment management for `OANDA_TOKEN`, `MCP_AUTH_TOKEN`, and OAuth configuration. Never put OANDA or OAuth secrets in client configuration.
- The remote MCP endpoint is protected independently from the OANDA token and is never cacheable (`Cache-Control: no-store`).
- Put this API behind authentication or a private network before exposing it publicly. The OANDA token stays server-side, but an unprotected endpoint could still be abused to consume quota.
- Use HTTPS at the ingress or reverse proxy.
