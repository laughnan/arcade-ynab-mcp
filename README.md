# arcade-ynab-mcp

An MCP server for [YNAB](https://www.ynab.com), built with
[arcade-mcp](https://github.com/ArcadeAI/arcade-mcp), hosted on Arcade Cloud via
`arcade deploy`, and used through Arcade MCP Gateways.

Each user connects their own YNAB account through OAuth 2.0 the first time they call a
tool. The server holds no secrets. See [docs/SPEC.md](docs/SPEC.md) for the full design.

## Tools

Phase 1 (read-only):

| Tool | What it does |
|---|---|
| `Ynab.GetUser` | The authenticated YNAB user |
| `Ynab.ListPlans` | The user's plans (budgets) |
| `Ynab.GetPlanSettings` | A plan's currency and date formats |
| `Ynab.GetMonth` | Ready to Assign, age of money, and every category's amounts for a month |
| `Ynab.ListMonths` | Monthly summaries, newest first |
| `Ynab.ListAccounts` / `Ynab.GetAccount` | Accounts and balances |
| `Ynab.ListCategories` / `Ynab.GetCategory` | Categories, amounts and goals |
| `Ynab.ListPayees` | Payees, optionally filtered by name |
| `Ynab.ListTransactions` / `Ynab.GetTransaction` | Transactions by date, account, category or payee |
| `Ynab.ListScheduledTransactions` | Upcoming and recurring transactions |
| `Ynab.ListMoneyMovements` | Money moved between categories |

Phase 2 (writes):

| Tool | What it does |
|---|---|
| `Ynab.CreateTransaction` | Add a purchase, income, transfer or split |
| `Ynab.UpdateTransactions` | Approve, recategorize, flag or edit up to 100 transactions at once |
| `Ynab.DeleteTransaction` | Delete one transaction (destructive) |
| `Ynab.ImportTransactions` | Import new transactions from linked accounts |
| `Ynab.AssignToCategory` | Set a category's assigned amount for a month |
| `Ynab.MoveMoney` | Move money between categories or Ready to Assign |
| `Ynab.CreateCategory` / `Ynab.UpdateCategory` | Create or edit categories and their targets |
| `Ynab.CreateCategoryGroup` / `Ynab.UpdateCategoryGroup` | Create or rename category groups |
| `Ynab.CreatePayee` / `Ynab.UpdatePayee` | Create or rename payees |
| `Ynab.CreateAccount` | Create an unlinked account |
| `Ynab.CreateScheduledTransaction` / `Ynab.UpdateScheduledTransaction` / `Ynab.DeleteScheduledTransaction` | Manage scheduled transactions |

Every tool is tagged read-only or write (and delete tools as destructive), so a gateway can
expose only the read tools.

Amounts are in currency units (not YNAB milliunits). Every tool defaults to the user's
most recently used plan.

## Setup

One-time setup of the YNAB OAuth app and the Arcade OAuth provider (ID `ynab`) is
described in [docs/SPEC.md](docs/SPEC.md#setup-one-time-done-by-the-maintainer-in-each-services-ui).

## Development

```bash
uv tool install arcade-mcp      # Arcade CLI
uv sync --extra dev             # project and dev dependencies
uv run pytest                   # unit tests (YNAB is mocked; no network)
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

To try the tools against your own YNAB account locally, log in with `arcade login`
(the OAuth flow runs through your Arcade project), then:

```bash
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

Never commit tokens or personal budget data. Tests use invented data only. YNAB tokens
are held by Arcade and injected per request; they are never exposed to the model.

## License

[MIT](LICENSE)
