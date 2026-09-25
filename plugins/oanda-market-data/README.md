# OANDA Market Data ChatGPT Plugin

This directory is a portable ChatGPT/Codex plugin package for the hosted OANDA MCP server.

## Requirements

The deployed MCP endpoint must be reachable at:

`https://api-get-ohlc-oanda.vercel.app/mcp/`

For no-auth ChatGPT use, configure the deployment with:

```text
MCP_PUBLIC_ACCESS=true
```

OAuth variables are not required in this mode. `MCP_AUTH_TOKEN` is also not required for the public MCP endpoint.

> Public mode is intentionally opt-in. Anyone who can reach the endpoint can call the read-only MCP tool and consume the deployment's OANDA/API quota.

## Build the upload ZIP

From the repository root:

```bash
cd plugins/oanda-market-data
zip -r ../../oanda-market-data-plugin.zip plugin.json mcp.json skills
```

Upload `oanda-market-data-plugin.zip` from ChatGPT's **Plugins → Add → upload** flow.

The ZIP must contain `plugin.json` and `mcp.json` at its root.
