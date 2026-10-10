"""Account tools."""

from enum import Enum
from typing import Annotated

from arcade_mcp_server import Context, tool

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.money import to_milliunits
from arcade_ynab.tools._common import (
    CREATES,
    DEFAULT_PLAN,
    READ_ONLY,
    YNAB_AUTH,
    PlanId,
    path_id,
    plan_path,
)


class NewAccountType(str, Enum):
    """Account types the YNAB API can create. Loan accounts must be created in the app."""

    CHECKING = "checking"
    SAVINGS = "savings"
    CASH = "cash"
    CREDIT_CARD = "creditCard"
    OTHER_ASSET = "otherAsset"
    OTHER_LIABILITY = "otherLiability"


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
    data = await client_from_context(context).get(
        plan_path(plan_id, f"/accounts/{path_id(account_id, 'account_id')}")
    )
    return shaping.account(data["account"])


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def create_account(
    context: Context,
    name: Annotated[str, "The account name."],
    account_type: Annotated[NewAccountType, "The kind of account."],
    balance: Annotated[
        float,
        "Starting balance in currency units. Use a negative number for money owed "
        "(credit cards and liabilities).",
    ],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The created account"]:
    """Create an unlinked account with a starting balance. Checking, savings, cash and
    credit card accounts are on budget; other assets and liabilities are tracking accounts."""
    data = await client_from_context(context).post(
        plan_path(plan_id, "/accounts"),
        {
            "account": {
                "name": name,
                "type": account_type.value,
                "balance": to_milliunits(balance),
            }
        },
    )
    return shaping.account(data["account"])
