#!/usr/bin/env python3
"""YNAB MCP server, hosted on Arcade Cloud with `arcade deploy`."""

import sys
from pathlib import Path
from types import ModuleType
from typing import cast

# When this file is run directly (as `arcade deploy` and `uv run` do), make the
# `arcade_ynab` package importable even if the project isn't installed.
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from arcade_mcp_server import MCPApp  # noqa: E402
from arcade_mcp_server.mcp_app import TransportType  # noqa: E402

from arcade_ynab.tools import (  # noqa: E402
    accounts,
    categories,
    money_movements,
    payees,
    plans,
    scheduled,
    transactions,
)

INSTRUCTIONS = """\
Tools for reading and managing a user's YNAB (You Need A Budget) data.

- YNAB calls a budget a "plan". Every tool defaults to the plan the user opened most
  recently ("last-used"); use ListPlans only when the user has several plans.
- Amounts are in the plan's currency units, not milliunits. Negative amounts are
  outflows, positive amounts are inflows.
- Category amounts use YNAB's terms: assigned, activity, available, and the plan has
  "Ready to Assign".
- Tools take IDs. Use the List tools to look up account, category and payee IDs.
- YNAB allows about 200 API requests per hour per user, so prefer the broadest tool
  that answers the question (for example GetMonth for a monthly overview).
- Before any write (creating, changing, moving or deleting), confirm the details with
  the user unless they were explicit.
"""

TOOL_MODULES: tuple[ModuleType, ...] = (
    plans,
    accounts,
    categories,
    payees,
    transactions,
    scheduled,
    money_movements,
)

app = MCPApp(name="ynab", version="0.2.0", instructions=INSTRUCTIONS, log_level="INFO")

for module in TOOL_MODULES:
    for obj in vars(module).values():
        if callable(obj) and hasattr(obj, "__tool_name__"):
            app.add_tool(obj)


if __name__ == "__main__":
    # "stdio" (default) for local MCP clients; "http" for streamable HTTP.
    # Arcade Cloud sets its own transport, host and port when deployed.
    transport = cast(TransportType, sys.argv[1] if len(sys.argv) > 1 else "stdio")
    app.run(transport=transport, host="127.0.0.1", port=8000)
