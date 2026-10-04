"""Account tools."""

from typing import Annotated

from arcade_mcp_server import Context, tool

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.tools._common import DEFAULT_PLAN, READ_ONLY, YNAB_AUTH, PlanId, plan_path


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_accounts(
    context: Context,
    include_closed: Annotated[bool, "Include closed accounts."] = False,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Accounts with type, budget status and balances"]:
    """List a plan's accounts with their type, whether they're on budget (or tracking only),
    and their working, cleared and uncleared balances."""
    data = await client_from_context(context).get_list(plan_path(plan_id, "/accounts"))
    accounts = [
        shaping.account(a)
        for a in shaping.live(data.get("accounts"))
        if include_closed or not a.get("closed")
    ]
    return {"accounts": accounts}


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def get_account(
    context: Context,
    account_id: Annotated[str, "The account ID. Use ListAccounts to find it."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "One account with type, budget status and balances"]:
    """Get a single account by ID."""
    data = await client_from_context(context).get(plan_path(plan_id, f"/accounts/{account_id}"))
    return shaping.account(data["account"])
