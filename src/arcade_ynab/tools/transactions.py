"""Transaction tools."""

from enum import Enum
from typing import Annotated, Any

from arcade_mcp_server import Context, tool
from arcade_mcp_server.exceptions import RetryableToolError
from typing_extensions import TypedDict

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.money import from_milliunits, to_milliunits
from arcade_ynab.tools._common import (
    CREATES,
    DEFAULT_LIMIT,
    DEFAULT_PLAN,
    DELETES,
    READ_ONLY,
    UPDATES,
    YNAB_AUTH,
    ClearedStatus,
    FlagColor,
    Limit,
    PlanId,
    clamp_limit,
    flag_value,
    plan_path,
    require_date,
    transfer_payee_id,
    validate_date,
)

MAX_BULK_UPDATE = 100


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


class Split(TypedDict, total=False):
    """One line of a split transaction."""

    amount: float
    category_id: str
    payee_id: str
    payee_name: str
    memo: str


def _split_lines(subtransactions: list[Split], total_milliunits: int) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for i, split in enumerate(subtransactions, start=1):
        if split.get("amount") is None:
            raise RetryableToolError(
                f"Split line {i} has no amount.",
                additional_prompt_content="Every split line needs an amount.",
            )
        line: dict[str, Any] = {"amount": to_milliunits(split["amount"])}
        for key in ("category_id", "payee_id", "payee_name", "memo"):
            if split.get(key):
                line[key] = split[key]
        lines.append(line)
    split_total = sum(line["amount"] for line in lines)
    if split_total != total_milliunits:
        raise RetryableToolError(
            f"Split lines add up to {from_milliunits(split_total)} but the transaction amount "
            f"is {from_milliunits(total_milliunits)}.",
            additional_prompt_content="Make the split amounts add up to the transaction amount.",
        )
    return lines


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def create_transaction(
    context: Context,
    account_id: Annotated[str, "The account the transaction belongs to."],
    date: Annotated[str, "Transaction date (YYYY-MM-DD). Future dates are not allowed."],
    amount: Annotated[
        float, "Amount in currency units. Negative for an outflow (spending), positive for inflow."
    ],
    payee_name: Annotated[
        str | None,
        "Payee name. Matches an existing payee by name or creates a new one. Ignored if "
        "payee_id is given.",
    ] = None,
    payee_id: Annotated[str | None, "An existing payee ID."] = None,
    transfer_account_id: Annotated[
        str | None,
        "For a transfer, the OTHER account's ID. The amount is from this account's point of "
        "view (negative moves money out of account_id). Don't combine with payee_id/payee_name.",
    ] = None,
    category_id: Annotated[
        str | None, "Category ID. Leave empty for transfers between budget accounts and splits."
    ] = None,
    memo: Annotated[str | None, "Optional memo."] = None,
    cleared: Annotated[ClearedStatus, "Cleared status."] = ClearedStatus.UNCLEARED,
    approved: Annotated[
        bool, "Mark as approved. Defaults to true, since the user asked for it explicitly."
    ] = True,
    flag_color: Annotated[FlagColor | None, "Optional flag color."] = None,
    subtransactions: Annotated[
        list[Split] | None,
        "Split lines. Each has an amount (same sign convention) and usually a category_id. "
        "The lines must add up to the transaction amount.",
    ] = None,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The created transaction"]:
    """Create a transaction: a purchase, income, a transfer between accounts, or a split."""
    if transfer_account_id and (payee_id or payee_name):
        raise RetryableToolError(
            "A transfer can't also have a payee.",
            additional_prompt_content="Use transfer_account_id alone, or a payee without it.",
        )
    if subtransactions and category_id:
        raise RetryableToolError(
            "A split transaction can't also have a category_id.",
            additional_prompt_content="Put categories on the split lines instead.",
        )

    client = client_from_context(context)
    milliunits = to_milliunits(amount)
    body: dict[str, Any] = {
        "account_id": account_id,
        "date": require_date(date, "date"),
        "amount": milliunits,
        "cleared": cleared.value,
        "approved": approved,
    }
    if transfer_account_id:
        body["payee_id"] = await transfer_payee_id(client, plan_id, transfer_account_id)
    elif payee_id:
        body["payee_id"] = payee_id
    elif payee_name:
        body["payee_name"] = payee_name
    if category_id:
        body["category_id"] = category_id
    if memo:
        body["memo"] = memo
    if flag_color:
        body["flag_color"] = flag_value(flag_color)
    if subtransactions:
        body["subtransactions"] = _split_lines(subtransactions, milliunits)

    data = await client.post(plan_path(plan_id, "/transactions"), {"transaction": body})
    return shaping.transaction(data["transaction"])


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES)
async def update_transactions(
    context: Context,
    transaction_ids: Annotated[
        list[str], f"IDs of the transactions to change (1-{MAX_BULK_UPDATE})."
    ],
    approved: Annotated[bool | None, "Set approved (true) or unapproved (false)."] = None,
    category_id: Annotated[str | None, "Recategorize. Not allowed on split transactions."] = None,
    payee_id: Annotated[str | None, "Change the payee to this existing payee."] = None,
    payee_name: Annotated[
        str | None, "Change the payee by name (matches or creates a payee)."
    ] = None,
    memo: Annotated[str | None, "Replace the memo. Pass an empty string to clear it."] = None,
    cleared: Annotated[ClearedStatus | None, "Set the cleared status."] = None,
    flag_color: Annotated[FlagColor | None, "Set the flag color, or 'none' to remove it."] = None,
    date: Annotated[str | None, "Change the date (YYYY-MM-DD)."] = None,
    amount: Annotated[
        float | None,
        "Change the amount in currency units. Only allowed when updating a single transaction; "
        "split amounts can't be changed.",
    ] = None,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The updated transactions"]:
    """Apply the same changes to one or more transactions in a single request, e.g. approve
    a batch, recategorize, mark cleared, flag, or fix a memo. Only the fields you pass change."""
    ids = [i for i in dict.fromkeys(transaction_ids) if i]
    if not 1 <= len(ids) <= MAX_BULK_UPDATE:
        raise RetryableToolError(
            f"Pass between 1 and {MAX_BULK_UPDATE} transaction IDs.",
            additional_prompt_content="Split larger batches into several calls.",
        )
    if amount is not None and len(ids) != 1:
        raise RetryableToolError(
            "amount can only be changed on a single transaction.",
            additional_prompt_content="Update amounts one transaction at a time.",
        )

    changes: dict[str, Any] = {}
    if approved is not None:
        changes["approved"] = approved
    if category_id:
        changes["category_id"] = category_id
    if payee_id:
        changes["payee_id"] = payee_id
    elif payee_name:
        changes["payee_name"] = payee_name
    if memo is not None:
        changes["memo"] = memo or None
    if cleared:
        changes["cleared"] = cleared.value
    if flag_color:
        changes["flag_color"] = flag_value(flag_color)
    if date:
        changes["date"] = require_date(date, "date")
    if amount is not None:
        changes["amount"] = to_milliunits(amount)
    if not changes:
        raise RetryableToolError(
            "No changes were given.",
            additional_prompt_content="Pass at least one field to change.",
        )

    data = await client_from_context(context).patch(
        plan_path(plan_id, "/transactions"),
        {"transactions": [{"id": i, **changes} for i in ids]},
    )
    updated = [shaping.transaction(t) for t in data.get("transactions") or []]
    return {"updated_count": len(updated), "transactions": updated}


@tool(requires_auth=YNAB_AUTH, metadata=DELETES)
async def delete_transaction(
    context: Context,
    transaction_id: Annotated[str, "The exact ID of the transaction to delete."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The deleted transaction"]:
    """Permanently delete one transaction. Only call this when the user has clearly asked to
    delete this specific transaction."""
    data = await client_from_context(context).delete(
        plan_path(plan_id, f"/transactions/{transaction_id}")
    )
    return {"deleted": True, "transaction": shaping.transaction(data["transaction"])}


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def import_transactions(
    context: Context,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "How many transactions were imported"]:
    """Ask YNAB to import new transactions from the plan's linked (direct import) accounts,
    like pressing "Import" in the app. Imported transactions arrive unapproved."""
    data = await client_from_context(context).request(
        "POST", plan_path(plan_id, "/transactions/import")
    )
    ids = data.get("transaction_ids") or []
    return {"imported_count": len(ids), "transaction_ids": ids}
