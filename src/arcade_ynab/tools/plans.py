"""User, plan and month tools."""

from typing import Annotated

from arcade_mcp_server import Context, tool

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.money import from_milliunits
from arcade_ynab.tools._common import (
    DEFAULT_PLAN,
    READ_ONLY,
    YNAB_AUTH,
    Month,
    PlanId,
    clamp_limit,
    normalize_month,
    plan_path,
)


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def get_user(context: Context) -> Annotated[dict, "The authenticated YNAB user's ID"]:
    """Get the YNAB user the current authorization belongs to."""
    data = await client_from_context(context).get("/user")
    return {"id": data["user"]["id"]}


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_plans(
    context: Context,
    include_accounts: Annotated[bool, "Also return each plan's open and closed accounts."] = False,
) -> Annotated[dict, "The user's plans (budgets) and the default plan, if one is set"]:
    """List the user's YNAB plans (budgets), most recently modified first."""
    data = await client_from_context(context).get_list(
        "/plans", include_accounts=str(include_accounts).lower()
    )
    plans = sorted(
        (shaping.plan(p) for p in shaping.live(data.get("plans"))),
        key=lambda p: p.get("last_modified_on") or "",
        reverse=True,
    )
    default = data.get("default_plan")
    return {"plans": plans, "default_plan_id": default["id"] if default else None}


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def get_plan_settings(
    context: Context,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The plan's currency and date formats"]:
    """Get a plan's currency and date format settings."""
    data = await client_from_context(context).get(plan_path(plan_id, "/settings"))
    return data["settings"]


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def get_month(
    context: Context,
    month: Month = "current",
    include_hidden: Annotated[bool, "Include hidden categories."] = False,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Month totals and every category's assigned, activity and available"]:
    """Get one plan month: Ready to Assign, age of money, income, total assigned and activity,
    plus every category's assigned, activity and available amounts and goal progress.

    Month totals cover all categories. When hidden categories are left out of the list,
    their combined amounts are in omitted_hidden_categories. Categories marked internal
    are YNAB system categories (e.g. Inflow: Ready to Assign), not budget lines.

    This is the best single call for "how is my budget doing this month?".
    """
    data = await client_from_context(context).get(
        plan_path(plan_id, f"/months/{normalize_month(month)}")
    )
    raw = data["month"]
    result = shaping.month(raw)
    categories = shaping.live(raw.get("categories"))
    result["categories"] = [
        shaping.category(c) for c in categories if include_hidden or not c.get("hidden")
    ]
    hidden = [c for c in categories if c.get("hidden")]
    if hidden and not include_hidden:
        # Month totals include hidden categories; summarize them so the list reconciles.
        result["omitted_hidden_categories"] = {
            "count": len(hidden),
            "assigned": from_milliunits(sum(c.get("budgeted", 0) for c in hidden)),
            "activity": from_milliunits(sum(c.get("activity", 0) for c in hidden)),
            "available": from_milliunits(sum(c.get("balance", 0) for c in hidden)),
        }
    return result


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_months(
    context: Context,
    limit: Annotated[int, "Maximum number of months to return, newest first (1-500)."] = 12,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Monthly summaries, newest first"]:
    """List monthly summaries for a plan (Ready to Assign, income, assigned, activity,
    age of money), newest first. Use GetMonth for category detail in a single month."""
    data = await client_from_context(context).get_list(plan_path(plan_id, "/months"))
    months = sorted(
        (shaping.month(m) for m in shaping.live(data.get("months"))),
        key=lambda m: m.get("month") or "",
        reverse=True,
    )
    kept, info = shaping.truncate(months, clamp_limit(limit))
    return {"months": kept, **info}
