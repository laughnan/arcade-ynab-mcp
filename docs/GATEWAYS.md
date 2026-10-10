# Gateways and write approval

This server exposes read tools and write tools from one deployment. Which tools a client
can call is decided by the [Arcade MCP Gateway](https://docs.arcade.dev/en/operate/governance/mcp-gateways)
it connects to, and whether a write needs a person's approval is decided by the MCP
client. This page describes the recommended setup.

## What is and isn't enforced

- **Enforced by the gateway:** which tools exist for a client. A tool that isn't
  selected in a gateway can't be called through it, whatever the model is told.
- **Enforced by the client (if configured):** asking a person before a tool runs.
- **Advisory only:**
  - The server's instructions ("confirm the details with the user before any write") are
    text for the model. A model can ignore them, and content it reads (a payee name, a
    memo, a web page) can try to talk it into a write.
  - Tool metadata (`read_only`, `destructive`, `idempotent`) describes each tool. It
    doesn't require approval by itself. Some clients use it as a hint; don't rely on it.

So the boundary that matters is: keep write tools out of the gateway you use every day,
and require approval wherever write tools are exposed.

Two limits remain even with that setup:

- **The token behind read tools can write.** Every tool uses the same full-access YNAB
  token (the server requests no `read-only` scope, so users authorize once; see the
  SPEC's authorization model). A read gateway hides the write *tools*; it doesn't make
  the credential read-only. The read tools don't write today, and tests check their
  metadata, but a bug in one could still change the plan.
- **Running the server locally skips the gateways.** `uv run src/arcade_ynab/server.py`
  (stdio or HTTP) registers every tool, reads and writes, with no gateway in front. In
  that mode the client's approval setting is the only control, so set it up as described
  below for every write tool.

## Recommended setup

Create two gateways in the Arcade dashboard, both in **Arcade Auth** mode so only
members of the Arcade project can use them.

### Read gateway (everyday use)

Select only these tools. They are all tagged `read_only=True`, and none of them call a
YNAB endpoint that changes data.

```text
Ynab.GetUser
Ynab.ListPlans
Ynab.GetPlanSettings
Ynab.GetMonth
Ynab.ListMonths
Ynab.ListAccounts
Ynab.GetAccount
Ynab.ListCategories
Ynab.GetCategory
Ynab.ListPayees
Ynab.ListTransactions
Ynab.GetTransaction
Ynab.ListScheduledTransactions
Ynab.ListMoneyMovements
Ynab.ReviewUnapproved
Ynab.FindOverspending
Ynab.SummarizeSpending
Ynab.ForecastCashFlow
Ynab.ReviewGoals
```

Connect this gateway in every client by default.

### Write gateway (only when you mean to change something)

Select only the write tools you actually use. For example, a gateway for approving and
categorizing transactions needs just one tool:

```text
Ynab.UpdateTransactions
```

Add others from the inventory below only when you need them, and leave out the
destructive ones unless you specifically need to delete.

The write gateway doesn't need read tools. A client that has both gateways connected
looks up IDs through the read gateway.

### All write tools (inventory, not a recommendation)

Every tool that can change YNAB, for reference when choosing. Don't select this whole
list as a gateway.

```text
Ynab.CreateTransaction
Ynab.UpdateTransactions
Ynab.DeleteTransaction
Ynab.ImportTransactions
Ynab.AssignToCategory
Ynab.MoveMoney
Ynab.CreateCategory
Ynab.UpdateCategory
Ynab.CreateCategoryGroup
Ynab.UpdateCategoryGroup
Ynab.CreatePayee
Ynab.UpdatePayee
Ynab.CreateAccount
Ynab.CreateScheduledTransaction
Ynab.UpdateScheduledTransaction
Ynab.DeleteScheduledTransaction
```

- `Ynab.DeleteTransaction` and `Ynab.DeleteScheduledTransaction` are tagged
  `destructive=True`.
- `Ynab.MoveMoney` and the `Create` tools other than `ImportTransactions` aren't
  idempotent: running them twice changes YNAB twice (two transactions, or the money
  moved twice). `Ynab.ImportTransactions` is tagged non-idempotent too, but a second
  run only pulls transactions that arrived since the first.

## Require approval for writes in the client

Connect the write gateway only in clients that can ask before running a tool, and turn
that on for every tool from the write gateway. Check your client's current docs; for
example:

- **Claude Code:** add the write gateway's server name to the `ask` permission list in
  your **user** settings (`~/.claude/settings.json`), so every tool from it prompts first
  in every directory you work in. A project's `.claude/settings.json` only applies inside
  that project, so it isn't enough for a server you use everywhere. Never add the server
  to `allow`:

  ```json
  {
    "permissions": {
      "ask": ["mcp__ynab-write"]
    }
  }
  ```

  Here `ynab-write` is whatever name you gave the server in your MCP config.

  An `ask` rule only helps while prompts are shown. Permission modes that skip prompts
  (`bypassPermissions`, `--dangerously-skip-permissions`) will run writes without
  asking, so don't use them in a session with the write gateway connected. `dontAsk`
  mode refuses the call instead of prompting, which is safe but means writes won't run.
- **Claude apps (custom connectors):** set each write tool's permission to require
  approval rather than "always allow".
- **Other clients:** turn off any "auto-run" or "always allow" setting for these tools.

If a client can't require approval per call, don't connect the write gateway to it.

## Verify the setup

After creating or changing a gateway:

1. **Read gateway exposes no writes.** List its tools (for example with the MCP
   Inspector, or by asking the client which YNAB tools it has) and check the list
   matches the read list above. Calling a write tool through it should fail because the
   tool doesn't exist.
2. **Write gateway needs approval.** In a client with the write gateway connected, ask
   for a harmless write (for example, flag a test transaction) and check the client asks
   before running it. Decline, and check nothing changed in YNAB.
3. **Gateway auth.** Connect to each gateway URL without signing in to Arcade and check
   the client can't list or call tools.

Repeat step 1 whenever tools are added to the server: a new tool isn't in either
gateway until you select it.
