# arcade-ynab-mcp

An MCP server for [YNAB](https://www.ynab.com), built with
[arcade-mcp](https://github.com/ArcadeAI/arcade-mcp) and designed to be hosted on
Arcade Cloud via `arcade deploy` and used through Arcade MCP Gateways.

> Status: early scaffold. Tools are being built out.

## Development

```bash
uv tool install arcade-mcp      # Arcade CLI
cp .env.example .env            # add your own secrets locally; .env is gitignored
uv run src/arcade_ynab/server.py          # stdio
uv run src/arcade_ynab/server.py http     # streamable HTTP on :8000
```

## Deploy

```bash
arcade login
arcade deploy -e src/arcade_ynab/server.py
```

Then add the server's tools to an MCP Gateway in the Arcade dashboard.

## Security

Never commit tokens or personal budget data. Secrets are provided at runtime via
`.env` (local) or Arcade secrets (deployed) and are never exposed to the model.
