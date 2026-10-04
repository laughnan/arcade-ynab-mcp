# arcade-ynab-mcp: Specification

Status: draft for review

This document describes what this repository will build: an MCP server that exposes the
[YNAB API](https://api.ynab.com) as agent tools, hosted on Arcade Cloud with
[Arcade Deploy](https://docs.arcade.dev/en/build/arcade-deploy) and used through an
[Arcade MCP Gateway](https://docs.arcade.dev/en/operate/governance/mcp-gateways).

## Goals

- **Arcade hosts the server.** The only deployment target is `arcade deploy`. This repo
  contains no Dockerfiles, self-hosting instructions or infrastructure code.
- **Every user brings their own YNAB account.** Users sign in to YNAB through OAuth 2.0
  when they first call a tool. Arcade stores and refreshes each user's token. The server
  never sees a personal access token or a client secret.
- **Follow Arcade's documented patterns.** Use the conventions in the Arcade docs for
  project layout, auth, tool metadata, errors and evals.
- **Tools built for agents, not a thin API wrapper.** Amounts come back in currency units,
  responses are trimmed to what a model needs, and composite tools answer common
  budgeting questions in one or two API calls.

## Non-goals

- Self-hosting, or running the server anywhere other than Arcade Cloud.
- Personal access token auth (`requires_secrets`). YNAB documents those tokens as
  personal-only, and this server is meant to work for any gateway user.
- Payee locations. These are GPS coordinates from YNAB's mobile apps. They add privacy
  risk and little value for agents.
- Delta sync and caching (`last_knowledge_of_server`). Arcade-hosted workers keep no
  state between calls.
- Distributing this broadly or submitting the YNAB OAuth app for review (see
  [YNAB OAuth application](#ynab-oauth-application)).

## Architecture

```text
MCP client ──► Arcade MCP Gateway ──► arcade_ynab (Arcade Cloud) ──► api.ynab.com/v1
                     │                          │
                     │                          └─ requires_auth=OAuth2(id="ynab")
                     └─ end users sign in            token injected per user via Context
                        (Arcade Auth mode)
```

1. **YNAB OAuth application.** One OAuth app, registered in YNAB Developer Settings,
   using the authorization code grant.
2. **Arcade custom OAuth 2.0 provider.** Configured in the Arcade dashboard with ID
   `ynab`. It holds the client ID and secret, runs the sign-in flow, and stores and
   refreshes user tokens.
3. **This server.** Deployed with `arcade deploy`. Every tool declares
   `requires_auth=OAuth2(id="ynab")` and reads the caller's access token from `Context`.
4. **MCP Gateway.** Picks which tools to expose. Its auth mode determines who can use it.

### Authorization model

- **No OAuth scopes.** YNAB offers two levels: full access (no scope) or `read-only`.
  Requesting different scopes on different tools would make users authorize twice and
  complicate token handling, so every tool requests full access.
- **Access control happens at the gateway.** Every tool carries `ToolMetadata` behavior
  flags (`read_only`, `destructive` and so on). A read-only gateway is built by
  selecting only the read tools.
- **Rate limits are per user.** YNAB allows 200 requests per hour per access token on a
  rolling window. Each user has their own token, so users don't share a budget. Tools
  must still keep API calls to a minimum (see [Design rules](#design-rules)).

## Setup (one-time, done by the maintainer in each service's UI)

The order matters, because each side needs a value from the other.

1. Run `arcade login`.
2. In the Arcade dashboard, go to **Connections → Connected apps → Add OAuth Provider →
   Custom Provider**. Copy the **Redirect URI** shown there and keep the form open.
3. In YNAB, go to **Developer Settings → New Application**:
   - Paste the redirect URI from step 2.
   - YNAB's terms don't allow "YNAB" in an app name unless it's preceded by "for"
     (for example, "Arcade for YNAB").
   - Optional: enable default plan selection, which lets tools accept `plan_id="default"`.
4. Finish the Arcade provider:

   | Field | Value |
   |---|---|
   | ID | `ynab` |
   | Client ID / Client Secret | from the YNAB app |
   | Authorization endpoint | `https://app.ynab.com/oauth/authorize` |
   | Token endpoint | `https://app.ynab.com/oauth/token` |
   | Refresh token endpoint | `https://app.ynab.com/oauth/token` |
   | PKCE | enabled (S256) |
   | Scopes | none |
   | Token request client authentication | client credentials in the POST body |
   | User info endpoint (optional) | `https://api.ynab.com/v1/user` |

   YNAB expects `client_id` and `client_secret` as POST body fields. Arcade defaults
   to HTTP Basic, and Arcade's docs warn that sending credentials in both places makes
   token exchanges fail.
5. Deploy with `arcade deploy -e src/arcade_ynab/server.py`.
6. Create an MCP Gateway, select the tools, and choose **Arcade Auth** mode. Users who are
   members of the Arcade project connect to `https://api.arcade.dev/mcp/<gateway-slug>`.

### YNAB OAuth application

New YNAB OAuth apps start in **Restricted Mode**, which allows up to 25 users besides
the app owner. This project is meant for a small, private gateway, so Restricted Mode is
acceptable and the app won't be submitted for review.

YNAB's OAuth application requirements (privacy policy, data handling, naming) still
apply to every app. How this project meets them is an open question (see
[Open questions](#open-questions)).

## Project layout

This follows Arcade's guide,
[Organize your MCP server and tools](https://docs.arcade.dev/en/build/create-tools/tool-basics/organize-mcp-tools).

```text
src/arcade_ynab/
├── server.py           # MCPApp("ynab"), registers tool modules, app.run() under __main__
├── client.py           # YNAB HTTP client: base URL, bearer auth, error mapping
├── money.py            # milliunit <-> decimal conversion and formatting helpers
├── shaping.py          # trims API objects into model-friendly dicts
└── tools/
    ├── __init__.py
    ├── plans.py        # user, plans, settings, months
    ├── accounts.py
    ├── categories.py
    ├── payees.py
    ├── transactions.py
    ├── scheduled.py
    ├── money_movements.py
    └── insights.py     # composite and analysis tools
tests/
├── conftest.py         # httpx.MockTransport fixtures built from API spec examples
└── test_*.py
evals/
└── eval_*.py           # arcade evals suites
docs/
└── SPEC.md
```

- `server.py` stays thin. Tools are defined with the `@tool` decorator in `tools/`
  modules, and `server.py` registers each one with `app.add_tool(...)`. It doesn't use
  `app.add_tools_from_module(...)`, because that looks up installed package metadata by
  module name and so only works for top-level packages, not submodules like
  `arcade_ynab.tools.plans`.
- `server.py` keeps `app.run()` inside `if __name__ == "__main__":`, which
  `arcade deploy` requires.
- The toolkit is named `ynab`, so tools are exposed as `Ynab.ListPlans`, and so on.

## Design rules

These apply to every tool.

1. **Auth.** Every tool uses `requires_auth=OAuth2(id="ynab")` and gets its token from
   `context` (`context.get_auth_token_or_empty()`). No tool uses `requires_secrets`.
2. **Metadata.** Every tool declares `ToolMetadata`:
   - `Classification(service_domains=[ServiceDomain.FINANCIAL_DATA])`. This is the
     closest domain Arcade offers; it has no budgeting domain.
   - A `Behavior` with `operations` and all four flags (`read_only`, `destructive`,
     `idempotent`, `open_world=True`) set explicitly. Arcade validates these at startup.
3. **Plans.** YNAB's API calls a budget a "plan" (v1.79+). Tools use the documented
   `/plans/...` paths, not the legacy `/budgets/...` aliases. `plan_id` is optional and
   defaults to `"last-used"`.
4. **Money.**
   - Tool inputs take amounts in currency units (for example, `-42.50`). The server
     converts them to milliunits.
   - Tool outputs return amounts as decimal values in currency units. They also include
     YNAB's `*_formatted` strings when the API provides them.
   - The model never deals with milliunits.
5. **Dates.** ISO 8601 (`YYYY-MM-DD`). Month parameters accept `YYYY-MM-01` or
   `"current"`.
6. **Response shaping.**
   - Drop entities with `deleted: true` and fields YNAB marks as deprecated.
   - Keep IDs so the model can chain calls.
   - Resolve names that are already in the response (account, payee, category).
7. **Bounded output.** List tools take a `limit` (default 100). When results are cut
   off, the response includes `truncated: true` and the total count.
8. **Few API calls.** Prefer broad endpoints (`/plans/{id}/months/{month}` returns every
   category for a month in one call) over looping. Composite tools use at most two or
   three YNAB requests. No tool loops over months or categories.
9. **Errors.** Use the exception classes in `arcade_mcp_server.exceptions`. Arcade's
   built-in adapters already turn `httpx` errors into upstream errors (429 becomes
   `UpstreamRateLimitError`). Add explicit handling only where the model needs a clearer
   message:
   - 401: the token was revoked or expired. The user needs to re-authorize.
   - 403.1 / 403.2: the YNAB subscription lapsed or the trial expired. Not retryable.
   - 404.2: the ID wasn't found. Retryable after the model looks up valid IDs.
   - 400: validation failed. Retryable, passing YNAB's `detail` back to the model.
10. **Writes are explicit.** Tools that write never infer a destructive action from a
    vague request. Delete tools take an exact ID.

## Tool catalog

`plan_id` is an implied optional parameter on every tool except `GetUser` and
`ListPlans`. Each heading lists the YNAB endpoints the tools call.

### Phase 1: Read-only core

These are read-only, idempotent and non-destructive.

**User and plans** (`GET /user`, `/plans`, `/plans/{id}/settings`, `/plans/{id}/months/...`)

| Tool | Parameters | Returns |
|---|---|---|
| `GetUser` | none | YNAB user ID |
| `ListPlans` | `include_accounts=False` | plans with ID, name, last modified, currency |
| `GetPlanSettings` | none | currency and date formats |
| `GetMonth` | `month="current"` | Ready to Assign, age of money, income, assigned, activity, every category with assigned, activity, available and goal fields |
| `ListMonths` | `limit` | monthly summaries: Ready to Assign, income, assigned, activity, age of money |

**Accounts** (`GET /plans/{id}/accounts[/{account_id}]`)

| Tool | Parameters | Returns |
|---|---|---|
| `ListAccounts` | `include_closed=False` | accounts with type, on/off budget, balance, cleared and uncleared balances |
| `GetAccount` | `account_id` | one account |

**Categories** (`GET /plans/{id}/categories`, `/plans/{id}/months/{month}/categories/{id}`)

| Tool | Parameters | Returns |
|---|---|---|
| `ListCategories` | `include_hidden=False` | category groups and categories with current-month amounts and goals |
| `GetCategory` | `category_id`, `month="current"` | one category for a month |

**Payees** (`GET /plans/{id}/payees`)

| Tool | Parameters | Returns |
|---|---|---|
| `ListPayees` | `name_contains=None`, `limit` | payees, with transfer payees marked |

**Transactions** (`GET /plans/{id}/transactions` and the account, category, payee and
month variants)

| Tool | Parameters | Returns |
|---|---|---|
| `ListTransactions` | `since_date`, `until_date`, `account_id`, `category_id`, `payee_id`, `type` (`unapproved` / `uncategorized`), `limit` | transactions, including splits. Uses the narrowest endpoint for the given filter. Only one of `account_id` / `category_id` / `payee_id` at a time |
| `GetTransaction` | `transaction_id` | one transaction, including splits |

**Scheduled transactions and money movements**

| Tool | Parameters | Returns |
|---|---|---|
| `ListScheduledTransactions` | `limit` | upcoming and recurring transactions with frequency and next date |
| `ListMoneyMovements` | `month=None` | money moved between categories or Ready to Assign, with movement groups |

### Phase 2: Writes

| Tool | Endpoint | Behavior |
|---|---|---|
| `CreateTransaction` | `POST /transactions` | create. Supports splits (`subtransactions`, which must add up to the amount), transfers (`transfer_account_id`, which looks up that account's transfer payee) and `payee_name`. Approved by default. Transfers between two budget accounts can't be categorized or split |
| `UpdateTransactions` | `PATCH /transactions` | update, idempotent. Applies the same changes to up to 100 transactions: approve, recategorize, payee, cleared, flag, memo, date. `amount` only when updating a single transaction. An empty string clears `category_id`, `payee_id` or `memo` |
| `DeleteTransaction` | `DELETE /transactions/{id}` | delete, destructive |
| `ImportTransactions` | `POST /transactions/import` | create. Pulls new transactions from linked accounts |
| `AssignToCategory` | `PATCH /months/{month}/categories/{id}` | update, idempotent. Sets the assigned amount for a month |
| `MoveMoney` | one month GET + up to two category PATCHes | update, not idempotent. Moves an amount between categories, or to and from Ready to Assign. The source must have at least that much available. Not atomic: it takes money out of the source first, so if the second write fails, the money is left in Ready to Assign and the error says so |
| `CreateCategory` / `UpdateCategory` | `POST` / `PATCH /categories` | create / update. Name, note, group, target (amount, date, repeat frequency, set-aside vs refill). YNAB's API can't hide or unhide categories |
| `CreateCategoryGroup` / `UpdateCategoryGroup` | `POST` / `PATCH /category_groups` | create / update |
| `CreatePayee` / `UpdatePayee` | `POST` / `PATCH /payees` | create / update (rename) |
| `CreateAccount` | `POST /accounts` | create. Name, type, starting balance |
| `CreateScheduledTransaction` / `UpdateScheduledTransaction` | `POST` / `PUT /scheduled_transactions` | create / update. YNAB replaces the whole object on `PUT`, so the update reads the current values first and changes only the fields passed. Dates must be in the future (within 5 years); if the next occurrence is today or past, the caller must pass a new date |
| `DeleteScheduledTransaction` | `DELETE /scheduled_transactions/{id}` | delete, destructive |

### Phase 3: Composite and analysis tools

These are read-only and use at most two or three YNAB requests each.

| Tool | Answers | Built from |
|---|---|---|
| `ReviewUnapproved` | "What needs my attention?" Unapproved transactions, each with a suggested category (with a confidence share) based on how that payee was categorized in the last `history_days`. No suggestion for splits or transfers, and transfers don't count as uncategorized. Looks back to `since_date` (default one year, YNAB's default) and returns that bound | unapproved transactions + recent transactions |
| `FindOverspending` | Categories with negative available balances, Ready to Assign, and candidate sources: the categories with the most money available, excluding credit card payment categories and categories with an underfunded target | `GetMonth` |
| `SummarizeSpending` | Spending grouped by category, category group or payee over a date range. Budget accounts only; split lines are counted in their own categories; transfers and income are excluded; refunds reduce spending | transactions for the range + accounts (+ categories when grouping by group) |
| `ForecastCashFlow` | Each account's ending and lowest balance over the next N days, from scheduled transactions (both sides of transfers). Same-day events are netted before checking the low point. `twiceAMonth` is approximated from `date_first`: that day and 15 days later, every month | accounts + scheduled transactions |
| `ReviewGoals` | Underfunded targets sorted by amount needed, counts of on-track and snoozed targets, and whether Ready to Assign covers the total | `GetMonth` |

## Testing

- **Unit tests (pytest):** mock YNAB with `httpx.MockTransport`, using response bodies
  built from the examples in YNAB's OpenAPI spec. Tests never call the real API and
  never use real budget data.
  - Cover: milliunit conversion, response shaping, error mapping, endpoint selection in
    `ListTransactions`, partial failure in `MoveMoney`, and the composite tools' math.
- **Tool evals (`arcade evals`):** suites in `evals/` check that models pick the right
  tool with the right arguments. Examples: "What did I spend on groceries last month?"
  should call `SummarizeSpending` or `ListTransactions` with the right dates. "Approve
  everything from Costco" should call `ListTransactions` and then `UpdateTransactions`.
- **Manual end-to-end testing:** after setup, run the server locally over stdio (OAuth
  works locally through `arcade login`), then deploy and call it through the gateway.

## Delivery plan

Each step below is a separate pull request.

1. **Clean up the scaffold.**
   - Remove the sample tools (`greet`, `whisper_secret`, `star_repo`).
   - Add the `client.py`, `money.py` and `shaping.py` foundations and the test harness.
   - Fix `pyproject.toml`: description, and align ruff's `target-version` with
     `requires-python`.
   - Remove the sample secret from `.env.example`.
   - Update the README with setup steps.
2. **Phase 1 read tools** with tests.
3. **First deploy:** `arcade deploy`, a read-only gateway, and an end-to-end check.
4. **Phase 2 write tools** with tests.
5. **Phase 3 composite tools** with tests and evals.

## Open questions

1. **YNAB OAuth application requirements.** YNAB requires OAuth apps to have a privacy
   policy covering data handling and retention, and to support data deletion requests.
   It's unclear which of these apply to an app that stays in Restricted Mode. How will
   this project meet them? This server stores no YNAB data itself; Arcade stores the
   tokens.
2. **Default plan selection.** Should the YNAB app enable it, so `plan_id="default"`
   works? Or is `last-used` enough?
3. **Separate read and write gateways.** Should the recommended setup be one read-only
   gateway plus a separate gateway that includes write tools?
4. **Python version on Arcade Cloud.** `requires-python` is `>=3.10`. Confirm which
   version Arcade Cloud runs, and set ruff and mypy targets to match.

## References

- YNAB API documentation: <https://api.ynab.com>
- YNAB OpenAPI spec: <https://api.ynab.com/papi/open_api_spec.yaml>
- Arcade Deploy: <https://docs.arcade.dev/en/build/arcade-deploy>
- Arcade custom OAuth 2.0 provider: <https://docs.arcade.dev/en/references/auth-providers/oauth2>
- Arcade tool metadata: <https://docs.arcade.dev/en/build/create-tools/tool-basics/add-tool-metadata>
- Arcade tool errors: <https://docs.arcade.dev/en/build/create-tools/error-handling/useful-tool-errors>
- Arcade MCP Gateways: <https://docs.arcade.dev/en/operate/governance/mcp-gateways/create-via-dashboard>
- Arcade evals: <https://docs.arcade.dev/en/build/create-tools/evaluate-tools/create-evaluation-suite>
