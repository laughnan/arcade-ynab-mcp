"""Composite and analysis tools.

Each tool answers a common budgeting question with at most three YNAB requests and does
the arithmetic server-side, so the model gets a compact answer instead of raw data.
"""

import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
from enum import Enum
from typing import Annotated, Any

from arcade_mcp_server import Context, tool
from arcade_mcp_server.exceptions import RetryableToolError

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.money import from_milliunits
from arcade_ynab.tools._common import (
    DEFAULT_LIMIT,
    DEFAULT_PLAN,
    READ_ONLY,
    YNAB_AUTH,
    Limit,
    Month,
    PlanId,
    clamp_limit,
    normalize_month,
    plan_path,
    validate_date,
)

Raw = dict[str, Any]

# YNAB's internal income category. Inflows here are income, not negative spending.
INCOME_CATEGORY_PREFIX = "Inflow:"
CREDIT_CARD_PAYMENTS_GROUP = "Credit Card Payments"


def _money(milliunits: int) -> float:
    value = from_milliunits(milliunits)
    assert value is not None
    return value


# --- ReviewUnapproved ---


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def review_unapproved(
    context: Context,
    since_date: Annotated[
        str | None,
        "Only include unapproved transactions on or after this date (YYYY-MM-DD). Defaults "
        "to one year ago, YNAB's default.",
    ] = None,
    history_days: Annotated[
        int, "How many days of approved transactions to learn payee categories from (7-365)."
    ] = 90,
    limit: Limit = DEFAULT_LIMIT,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Unapproved transactions with suggested categories"]:
    """List transactions waiting for approval, each with a suggested category based on how
    the same payee was categorized recently. Use UpdateTransactions to approve or
    recategorize them after the user confirms."""
    history_days = max(7, min(int(history_days), 365))
    today = date.today()
    # YNAB defaults since_date to one year ago; send it explicitly so the response can
    # say how far back it looked.
    inbox_since = (
        validate_date(since_date, "since_date") or (today - timedelta(days=365)).isoformat()
    )
    client = client_from_context(context)
    unapproved_data = await client.get_list(
        plan_path(plan_id, "/transactions"), type="unapproved", since_date=inbox_since
    )
    since = (today - timedelta(days=history_days)).isoformat()
    history_data = await client.get_list(plan_path(plan_id, "/transactions"), since_date=since)

    votes: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for t in shaping.live(history_data.get("transactions")):
        if (
            t.get("approved")
            and t.get("payee_id")
            and t.get("category_id")
            and not t.get("transfer_account_id")
            and not shaping.live(t.get("subtransactions"))
        ):
            votes[t["payee_id"]][(t["category_id"], t.get("category_name") or "")] += 1

    rows = []
    for t in sorted(
        shaping.live(unapproved_data.get("transactions")),
        key=lambda t: t.get("date") or "",
        reverse=True,
    ):
        row = shaping.transaction(t)
        payee_votes = votes.get(t.get("payee_id") or "")
        # Splits can't be recategorized as a whole, and transfers don't need a category.
        is_split = bool(shaping.live(t.get("subtransactions")))
        if payee_votes and not t.get("transfer_account_id") and not is_split:
            (category_id, category_name), count = payee_votes.most_common(1)[0]
            row["suggested_category"] = {
                "id": category_id,
                "name": category_name,
                "confidence": round(count / sum(payee_votes.values()), 2),
                "matches_current": category_id == t.get("category_id"),
            }
        rows.append(row)

    kept, info = shaping.truncate(rows, clamp_limit(limit))
    return {
        "since_date": inbox_since,
        "unapproved": kept,
        "uncategorized_count": sum(
            1
            for r in rows
            if not r.get("category_id")
            and not r.get("subtransactions")
            and not r.get("transfer_account_id")
        ),
        **info,
    }


# --- FindOverspending ---


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def find_overspending(
    context: Context,
    month: Month = "current",
    max_candidates: Annotated[int, "How many categories to suggest as sources (1-20)."] = 5,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Overspent categories and where money could come from"]:
    """Find overspent categories (negative available) in a month and suggest where to cover
    them from: Ready to Assign first, then the categories with the most money available
    that aren't saving toward an underfunded target. Use MoveMoney to cover after the user
    picks."""
    data = await client_from_context(context).get(
        plan_path(plan_id, f"/months/{normalize_month(month)}")
    )
    raw_month = data["month"]
    categories = [c for c in shaping.live(raw_month.get("categories")) if not c.get("hidden")]

    overspent = sorted(
        (c for c in categories if c.get("balance", 0) < 0),
        key=lambda c: c["balance"],
    )
    candidates = sorted(
        (
            c
            for c in categories
            if c.get("balance", 0) > 0
            and c.get("category_group_name") != CREDIT_CARD_PAYMENTS_GROUP
            and not (c.get("goal_under_funded") or 0) > 0
        ),
        key=lambda c: c["balance"],
        reverse=True,
    )[: max(1, min(int(max_candidates), 20))]

    total_overspent = -sum(c["balance"] for c in overspent)
    ready_to_assign = raw_month.get("to_be_budgeted") or 0
    return {
        "month": raw_month.get("month"),
        "total_overspent": _money(total_overspent),
        "ready_to_assign": _money(ready_to_assign),
        "ready_to_assign_covers_all": ready_to_assign >= total_overspent,
        "overspent": [
            {
                "category_id": c["id"],
                "name": c.get("name"),
                "category_group_name": c.get("category_group_name"),
                "overspent_by": _money(-c["balance"]),
            }
            for c in overspent
        ],
        "candidates": [
            {
                "category_id": c["id"],
                "name": c.get("name"),
                "category_group_name": c.get("category_group_name"),
                "available": _money(c["balance"]),
            }
            for c in candidates
        ],
    }


# --- SummarizeSpending ---


class GroupBy(str, Enum):
    CATEGORY = "category"
    CATEGORY_GROUP = "category_group"
    PAYEE = "payee"


def _spending_lines(t: Raw) -> list[Raw]:
    """Expand a transaction into lines that each carry one category and payee."""
    subs = shaping.live(t.get("subtransactions"))
    if not subs:
        return [t]
    return [
        {
            **s,
            "payee_id": s.get("payee_id") or t.get("payee_id"),
            "payee_name": s.get("payee_name") or t.get("payee_name"),
        }
        for s in subs
    ]


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def summarize_spending(
    context: Context,
    since_date: Annotated[str, "Start of the period (YYYY-MM-DD), inclusive."],
    until_date: Annotated[
        str | None, "End of the period (YYYY-MM-DD), inclusive. Defaults to today."
    ] = None,
    group_by: Annotated[GroupBy, "Group spending by category, category group or payee."] = (
        GroupBy.CATEGORY
    ),
    limit: Annotated[int, "Maximum number of groups to return, biggest first (1-500)."] = 25,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Spending totals per group, biggest first"]:
    """Summarize spending over a date range, grouped by category, category group or payee.

    Counts budget-account transactions only. Transfers between accounts and income
    (inflows to Ready to Assign) are excluded; refunds reduce spending in their category.
    Spending is reported as a positive number."""
    since = validate_date(since_date, "since_date")
    until = validate_date(until_date, "until_date") or date.today().isoformat()
    if since and since > until:
        raise RetryableToolError(
            "since_date is after until_date.",
            additional_prompt_content="Swap or fix the dates.",
        )

    client = client_from_context(context)
    tx_data = await client.get(
        plan_path(plan_id, "/transactions"), since_date=since, until_date=until
    )
    accounts_data = await client.get(plan_path(plan_id, "/accounts"))
    on_budget = {a["id"] for a in shaping.live(accounts_data.get("accounts")) if a.get("on_budget")}

    group_names: dict[str, tuple[str, str]] = {}
    if group_by is GroupBy.CATEGORY_GROUP:
        cat_data = await client.get(plan_path(plan_id, "/categories"))
        for g in shaping.live(cat_data.get("category_groups")):
            for c in g.get("categories") or []:
                group_names[c["id"]] = (g["id"], g.get("name") or "")

    totals: dict[str, int] = defaultdict(int)
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for t in shaping.live(tx_data.get("transactions")):
        if t.get("account_id") not in on_budget or (t.get("date") or "") > until:
            continue
        for line in _spending_lines(t):
            category_name = line.get("category_name") or ""
            if line.get("transfer_account_id") or (
                line.get("amount", 0) > 0 and category_name.startswith(INCOME_CATEGORY_PREFIX)
            ):
                continue
            category_id = line.get("category_id")
            if group_by is GroupBy.PAYEE:
                key, name = line.get("payee_id") or "", line.get("payee_name") or "(no payee)"
            elif group_by is GroupBy.CATEGORY_GROUP:
                key, name = group_names.get(category_id or "", ("", "Uncategorized"))
            else:
                key, name = category_id or "", category_name or "Uncategorized"
            totals[key] -= line.get("amount", 0)
            counts[key] += 1
            names[key] = name

    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    groups = [
        shaping.compact(
            {
                "id": key or None,
                "name": names[key],
                "spent": _money(total),
                "transaction_count": counts[key],
            }
        )
        for key, total in ranked
    ]
    kept, info = shaping.truncate(groups, clamp_limit(limit))
    return {
        "since_date": since,
        "until_date": until,
        "group_by": group_by.value,
        "total_spent": _money(sum(totals.values())),
        "groups": kept,
        **info,
    }


# --- ForecastCashFlow ---

_STEP_DAYS = {"daily": 1, "weekly": 7, "everyOtherWeek": 14, "every4Weeks": 28}
_STEP_MONTHS = {
    "monthly": 1,
    "everyOtherMonth": 2,
    "every3Months": 3,
    "every4Months": 4,
    "twiceAYear": 6,
    "yearly": 12,
    "everyOtherYear": 24,
}


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def occurrences(first: date, frequency: str, end: date, anchor: date | None = None) -> list[date]:
    """Dates from ``first`` (the next occurrence) through ``end`` for a YNAB frequency.

    ``anchor`` is the schedule's original date (``date_first``). It only matters for
    ``twiceAMonth``, which isn't a constant interval: it's approximated as the anchor's day
    and 15 days after it, repeating monthly. Other frequencies step from ``first``.
    """
    if first > end:
        return []
    if frequency == "never":
        return [first]
    dates: list[date] = []
    if frequency in _STEP_DAYS:
        step = timedelta(days=_STEP_DAYS[frequency])
        current = first
        while current <= end:
            dates.append(current)
            current += step
        return dates
    if frequency == "twiceAMonth":
        start = anchor if anchor and anchor <= first else first
        halves = (start, start + timedelta(days=15))
        n = 0
        while _add_months(start, n) <= end:
            dates.extend(d for half in halves if first <= (d := _add_months(half, n)) <= end)
            n += 1
        return sorted(dates)
    months = _STEP_MONTHS.get(frequency)
    if months is None:
        return [first]
    n = 0
    while (current := _add_months(first, n * months)) <= end:
        dates.append(current)
        n += 1
    return dates


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def forecast_cash_flow(
    context: Context,
    days: Annotated[int, "How many days ahead to forecast (1-365)."] = 30,
    account_id: Annotated[
        str | None, "Only forecast this account. Defaults to all open budget accounts."
    ] = None,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Projected balances per account from scheduled transactions"]:
    """Project account balances over the next N days from current balances and scheduled
    transactions (bills, paychecks, transfers). Reports each account's ending balance and
    its lowest point, to spot upcoming shortfalls. Doesn't include unscheduled spending."""
    days = max(1, min(int(days), 365))
    today = date.today()
    end = today + timedelta(days=days)

    client = client_from_context(context)
    accounts_data = await client.get(plan_path(plan_id, "/accounts"))
    scheduled_data = await client.get(plan_path(plan_id, "/scheduled_transactions"))

    accounts = {
        a["id"]: a
        for a in shaping.live(accounts_data.get("accounts"))
        if not a.get("closed") and (a["id"] == account_id if account_id else a.get("on_budget"))
    }
    if account_id and account_id not in accounts:
        raise RetryableToolError(
            f"Account {account_id} wasn't found or is closed.",
            additional_prompt_content="Use ListAccounts to find an open account ID.",
        )

    events: dict[str, list[Raw]] = defaultdict(list)
    for s in shaping.live(scheduled_data.get("scheduled_transactions")):
        first = date.fromisoformat(s["date_next"])
        anchor = date.fromisoformat(s["date_first"]) if s.get("date_first") else None
        for when in occurrences(first, s.get("frequency") or "never", end, anchor):
            if when < today:
                continue
            legs: list[tuple[str, int]] = [(s.get("account_id") or "", s["amount"])]
            if s.get("transfer_account_id"):
                legs.append((s["transfer_account_id"], -s["amount"]))
            for leg_account, amount in legs:
                if leg_account in accounts:
                    events[leg_account].append(
                        {
                            "date": when.isoformat(),
                            "amount": amount,
                            "payee_name": s.get("payee_name"),
                            "category_name": s.get("category_name"),
                            "scheduled_transaction_id": s["id"],
                        }
                    )

    forecasts = []
    for acct_id, account in accounts.items():
        balance = account.get("balance", 0)
        lowest, lowest_date = balance, today.isoformat()
        timeline = sorted(events.get(acct_id, []), key=lambda e: e["date"])
        # Net each day's events before checking the low point, so the order YNAB lists
        # same-day items in (e.g. a paycheck and a bill) doesn't create a false shortfall.
        net_by_day: dict[str, int] = defaultdict(int)
        for event in timeline:
            net_by_day[event["date"]] += event["amount"]
        for day in sorted(net_by_day):
            balance += net_by_day[day]
            if balance < lowest:
                lowest, lowest_date = balance, day
        forecasts.append(
            {
                "account_id": acct_id,
                "name": account.get("name"),
                "starting_balance": _money(account.get("balance", 0)),
                "ending_balance": _money(balance),
                "lowest_balance": _money(lowest),
                "lowest_balance_date": lowest_date,
                "goes_negative": lowest < 0,
                "upcoming": [
                    shaping.compact({**e, "amount": _money(e["amount"])}) for e in timeline
                ],
            }
        )
    forecasts.sort(key=lambda f: f["lowest_balance"])
    return {"from_date": today.isoformat(), "to_date": end.isoformat(), "accounts": forecasts}


# --- ReviewGoals ---


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def review_goals(
    context: Context,
    month: Month = "current",
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Underfunded targets and what it takes to fund them"]:
    """Review category targets (goals) for a month: which are underfunded and by how much,
    how many are on track, and whether Ready to Assign can cover the shortfall. Use
    AssignToCategory or MoveMoney to fund them after the user confirms."""
    data = await client_from_context(context).get(
        plan_path(plan_id, f"/months/{normalize_month(month)}")
    )
    raw_month = data["month"]
    with_goals = [
        c
        for c in shaping.live(raw_month.get("categories"))
        if c.get("goal_type") and not c.get("hidden")
    ]
    snoozed = [c for c in with_goals if c.get("goal_snoozed_at")]
    active = [c for c in with_goals if not c.get("goal_snoozed_at")]
    underfunded = sorted(
        (c for c in active if (c.get("goal_under_funded") or 0) > 0),
        key=lambda c: c["goal_under_funded"],
        reverse=True,
    )

    needed = sum(c["goal_under_funded"] for c in underfunded)
    ready_to_assign = raw_month.get("to_be_budgeted") or 0
    return {
        "month": raw_month.get("month"),
        "targets": len(active),
        "on_track": len(active) - len(underfunded),
        "snoozed": len(snoozed),
        "total_needed": _money(needed),
        "ready_to_assign": _money(ready_to_assign),
        "ready_to_assign_covers_all": ready_to_assign >= needed,
        "underfunded": [
            shaping.compact(
                {
                    "category_id": c["id"],
                    "name": c.get("name"),
                    "category_group_name": c.get("category_group_name"),
                    "needed_this_month": _money(c["goal_under_funded"]),
                    "assigned": _money(c.get("budgeted", 0)),
                    "available": _money(c.get("balance", 0)),
                    "goal_type": c.get("goal_type"),
                    "target": from_milliunits(c.get("goal_target")),
                    "target_date": c.get("goal_target_date"),
                    "percentage_complete": c.get("goal_percentage_complete"),
                }
            )
            for c in underfunded
        ],
    }
