"""Transaction tools."""

from enum import Enum
from typing import Annotated

from arcade_mcp_server import Context, tool
from arcade_mcp_server.exceptions import RetryableToolError

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.tools._common import (
    DEFAULT_LIMIT,
    DEFAULT_PLAN,
    READ_ONLY,
    YNAB_AUTH,
    Limit,
    PlanId,
    clamp_limit,
    plan_path,
    validate_date,
)


class TransactionType(str, Enum):
    UNAPPROVED = "unapproved"
    UNCATEGORIZED = "uncategorized"


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_transactions(
    context: Context,
    since_date: Annotated[
        str | None,
        "Only include transactions on or after this date (YYYY-MM-DD). YNAB defaults to "
        "one year ago when omitted.",
    ] = None,
    until_date: Annotated[
        str | None, "Only include transactions on or before this date (YYYY-MM-DD)."
    ] = None,
    account_id: Annotated[str | None, "Only include transactions in this account."] = None,
    category_id: Annotated[str | None, "Only include transactions in this category."] = None,
    payee_id: Annotated[str | None, "Only include transactions with this payee."] = None,
    transaction_type: Annotated[
        TransactionType | None, "Only include unapproved or uncategorized transactions."
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Transactions, newest first"]:
    """List transactions, newest first, optionally filtered by date range and by ONE of
    account, category or payee.

    Amounts are in currency units: negative is an outflow, positive is an inflow. Split
    transactions include their subtransactions. When filtering by category or payee, split
    lines are returned as their own rows with type 'subtransaction'."""
    scopes = {"accounts": account_id, "categories": category_id, "payees": payee_id}
    chosen = {k: v for k, v in scopes.items() if v}
    if len(chosen) > 1:
        raise RetryableToolError(
            "Filter by only one of account_id, category_id or payee_id.",
            additional_prompt_content=(
                "Pick the most selective filter and narrow the results yourself, or make "
                "separate calls."
            ),
        )
    suffix = "/transactions"
    if chosen:
        resource, resource_id = next(iter(chosen.items()))
        suffix = f"/{resource}/{resource_id}/transactions"

    data = await client_from_context(context).get(
        plan_path(plan_id, suffix),
        since_date=validate_date(since_date, "since_date"),
        until_date=validate_date(until_date, "until_date"),
        type=transaction_type.value if transaction_type else None,
    )
    transactions = sorted(
        (shaping.transaction(t) for t in shaping.live(data.get("transactions"))),
        key=lambda t: t.get("date") or "",
        reverse=True,
    )
    kept, info = shaping.truncate(transactions, clamp_limit(limit))
    return {"transactions": kept, **info}


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def get_transaction(
    context: Context,
    transaction_id: Annotated[str, "The transaction ID. Use ListTransactions to find it."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "One transaction, including any split lines"]:
    """Get a single transaction by ID, including its subtransactions if it is a split."""
    data = await client_from_context(context).get(
        plan_path(plan_id, f"/transactions/{transaction_id}")
    )
    return shaping.transaction(data["transaction"])
