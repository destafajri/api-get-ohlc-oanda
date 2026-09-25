# ChatGPT Plugin Setup

The repository includes a portable plugin package in `plugins/oanda-market-data/`.

## No-auth deployment

The MCP server remains authenticated by default. To intentionally expose the read-only MCP endpoint without OAuth or a static Bearer token, set:

```text
MCP_PUBLIC_ACCESS=true
```

For ChatGPT Web, also allow the ChatGPT origins:

```text
MCP_ALLOWED_ORIGINS=https://chatgpt.com,https://chat.openai.com
```

Then redeploy the service.

No OAuth configuration is required for this mode:

```text
MCP_PUBLIC_URL
MCP_OAUTH_ISSUER_URL
MCP_OAUTH_JWKS_URL
```

may remain unset.

`MCP_AUTH_TOKEN` is also unnecessary for public mode.

## Security trade-off

Public mode exposes the MCP endpoint to anyone who can reach its URL. The current MCP surface is read-only and exposes `get_ohlc` and `list_instruments`, but callers can still consume server and OANDA request quota.

Keep `MCP_PUBLIC_ACCESS=false` unless public access is intentional.

## ChatGPT Web MCP App

Create an MCP App in ChatGPT with:

```text
Name: OANDA Market Data
Server URL: https://api-get-ohlc-oanda.vercel.app/mcp/
Authentication: No authentication
```

The discovered tools should include `get_ohlc` and `list_instruments`.

## Build the plugin archive

```bash
cd plugins/oanda-market-data
zip -r ../../oanda-market-data-plugin.zip plugin.json mcp.json skills
```

Then upload the ZIP in ChatGPT under **Plugins → Add**.

The package points to:

`https://api-get-ohlc-oanda.vercel.app/mcp/`
