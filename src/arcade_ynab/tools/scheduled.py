"""Scheduled transaction tools."""

from enum import Enum
from typing import Annotated, Any

from arcade_mcp_server import Context, tool
from arcade_mcp_server.exceptions import RetryableToolError

from arcade_ynab import shaping
from arcade_ynab.client import YnabClient, client_from_context
from arcade_ynab.money import to_milliunits
from arcade_ynab.tools._common import (
    CREATES,
    DEFAULT_LIMIT,
    DEFAULT_PLAN,
    DELETES,
    READ_ONLY,
    UPDATES,
    YNAB_AUTH,
    FlagColor,
    Limit,
    PlanId,
    clamp_limit,
    flag_value,
    plan_path,
    require_date,
    transfer_payee_id,
)


class Frequency(str, Enum):
    NEVER = "never"
    DAILY = "daily"
    WEEKLY = "weekly"
    EVERY_OTHER_WEEK = "everyOtherWeek"
    TWICE_A_MONTH = "twiceAMonth"
    EVERY_4_WEEKS = "every4Weeks"
    MONTHLY = "monthly"
    EVERY_OTHER_MONTH = "everyOtherMonth"
    EVERY_3_MONTHS = "every3Months"
    EVERY_4_MONTHS = "every4Months"
    TWICE_A_YEAR = "twiceAYear"
    YEARLY = "yearly"
    EVERY_OTHER_YEAR = "everyOtherYear"


NextDate = Annotated[
    str, "Date of the next occurrence (YYYY-MM-DD), in the future and within 5 years."
]
Amount = Annotated[float, "Amount in currency units. Negative for outflows, positive for inflows."]
PayeeName = Annotated[str | None, "Payee name (matches or creates a payee)."]
PayeeId = Annotated[str | None, "An existing payee ID."]
TransferAccountId = Annotated[
    str | None, "For a scheduled transfer, the OTHER account's ID. Don't combine with a payee."
]
CategoryId = Annotated[str | None, "Category ID. Split scheduled transactions aren't supported."]


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_scheduled_transactions(
    context: Context,
    limit: Limit = DEFAULT_LIMIT,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Scheduled transactions, soonest first"]:
    """List upcoming and recurring scheduled transactions, soonest first, with their
    frequency and next date. Amounts are negative for outflows, positive for inflows."""
    data = await client_from_context(context).get(plan_path(plan_id, "/scheduled_transactions"))
    scheduled = sorted(
        (
            shaping.scheduled_transaction(t)
            for t in shaping.live(data.get("scheduled_transactions"))
        ),
        key=lambda t: t.get("date_next") or "",
    )
    kept, info = shaping.truncate(scheduled, clamp_limit(limit))
    return {"scheduled_transactions": kept, **info}


async def _apply_fields(
    client: YnabClient,
    plan_id: str,
    body: dict[str, Any],
    *,
    payee_name: str | None,
    payee_id: str | None,
    transfer_account_id: str | None,
    category_id: str | None,
    memo: str | None,
    flag_color: FlagColor | None,
) -> None:
    if transfer_account_id and (payee_id or payee_name):
        raise RetryableToolError(
            "A transfer can't also have a payee.",
            additional_prompt_content="Use transfer_account_id alone, or a payee without it.",
        )
    if transfer_account_id:
        body["payee_id"] = await transfer_payee_id(client, plan_id, transfer_account_id)
        body.pop("payee_name", None)
    elif payee_id:
        body["payee_id"] = payee_id
        body.pop("payee_name", None)
    elif payee_name:
        body["payee_name"] = payee_name
        body.pop("payee_id", None)
    if category_id:
        body["category_id"] = category_id
    if memo is not None:
        body["memo"] = memo or None
    if flag_color:
        body["flag_color"] = flag_value(flag_color)


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def create_scheduled_transaction(
    context: Context,
    account_id: Annotated[str, "The account it will be entered in."],
    date: NextDate,
    amount: Amount,
    frequency: Annotated[Frequency, "How often it repeats. 'never' means it happens once."],
    payee_name: PayeeName = None,
    payee_id: PayeeId = None,
    transfer_account_id: TransferAccountId = None,
    category_id: CategoryId = None,
    memo: Annotated[str | None, "Optional memo."] = None,
    flag_color: Annotated[FlagColor | None, "Optional flag color."] = None,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The created scheduled transaction"]:
    """Schedule an upcoming or recurring transaction (bill, paycheck, transfer)."""
    client = client_from_context(context)
    body: dict[str, Any] = {
        "account_id": account_id,
        "date": require_date(date, "date"),
        "amount": to_milliunits(amount),
        "frequency": frequency.value,
    }
    await _apply_fields(
        client,
        plan_id,
        body,
        payee_name=payee_name,
        payee_id=payee_id,
        transfer_account_id=transfer_account_id,
        category_id=category_id,
        memo=memo or None,
        flag_color=flag_color,
    )
    data = await client.post(
        plan_path(plan_id, "/scheduled_transactions"), {"scheduled_transaction": body}
    )
    return shaping.scheduled_transaction(data["scheduled_transaction"])


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES)
async def update_scheduled_transaction(
    context: Context,
    scheduled_transaction_id: Annotated[str, "The scheduled transaction to change."],
    date: Annotated[str | None, "New next date (YYYY-MM-DD), in the future."] = None,
    amount: Annotated[float | None, "New amount in currency units."] = None,
    frequency: Annotated[Frequency | None, "New repeat frequency."] = None,
    account_id: Annotated[str | None, "Move it to this account."] = None,
    payee_name: PayeeName = None,
    payee_id: PayeeId = None,
    transfer_account_id: TransferAccountId = None,
    category_id: CategoryId = None,
    memo: Annotated[str | None, "New memo. Pass an empty string to clear it."] = None,
    flag_color: Annotated[FlagColor | None, "New flag color, or 'none' to remove it."] = None,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The updated scheduled transaction"]:
    """Change a scheduled transaction. Only the fields you pass change."""
    client = client_from_context(context)
    path = plan_path(plan_id, f"/scheduled_transactions/{scheduled_transaction_id}")
    current = (await client.get(path))["scheduled_transaction"]

    # YNAB replaces the whole scheduled transaction, so start from its current values.
    body: dict[str, Any] = {
        "account_id": account_id or current["account_id"],
        "date": require_date(date, "date") if date else current["date_next"],
        "amount": to_milliunits(amount) if amount is not None else current["amount"],
        "frequency": frequency.value if frequency else current["frequency"],
    }
    for key in ("payee_id", "category_id", "memo", "flag_color"):
        if current.get(key) is not None:
            body[key] = current[key]
    await _apply_fields(
        client,
        plan_id,
        body,
        payee_name=payee_name,
        payee_id=payee_id,
        transfer_account_id=transfer_account_id,
        category_id=category_id,
        memo=memo,
        flag_color=flag_color,
    )
    data = await client.put(path, {"scheduled_transaction": body})
    return shaping.scheduled_transaction(data["scheduled_transaction"])


@tool(requires_auth=YNAB_AUTH, metadata=DELETES)
async def delete_scheduled_transaction(
    context: Context,
    scheduled_transaction_id: Annotated[str, "The exact ID of the scheduled transaction."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The deleted scheduled transaction"]:
    """Permanently delete a scheduled transaction. Only call this when the user has clearly
    asked to delete this specific scheduled transaction."""
    data = await client_from_context(context).delete(
        plan_path(plan_id, f"/scheduled_transactions/{scheduled_transaction_id}")
    )
    return {
        "deleted": True,
        "scheduled_transaction": shaping.scheduled_transaction(data["scheduled_transaction"]),
    }
