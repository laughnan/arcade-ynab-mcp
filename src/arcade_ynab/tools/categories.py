"""Category tools."""

from typing import Annotated

from arcade_mcp_server import Context, tool

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.tools._common import (
    DEFAULT_PLAN,
    READ_ONLY,
    YNAB_AUTH,
    Month,
    PlanId,
    normalize_month,
    plan_path,
)


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_categories(
    context: Context,
    include_hidden: Annotated[bool, "Include hidden category groups and categories."] = False,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Category groups with their categories and current-month amounts"]:
    """List a plan's category groups and categories, with each category's assigned,
    activity and available amounts for the current month and its goal, if any.

    Use GetMonth for amounts in a different month."""
    data = await client_from_context(context).get_list(plan_path(plan_id, "/categories"))
    groups = [
        shaping.category_group(g, include_hidden)
        for g in shaping.live(data.get("category_groups"))
        if include_hidden or not g.get("hidden")
    ]
    return {"category_groups": groups}


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def get_category(
    context: Context,
    category_id: Annotated[str, "The category ID. Use ListCategories to find it."],
    month: Month = "current",
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "One category's assigned, activity, available and goal for a month"]:
    """Get a single category's amounts and goal progress for a given month."""
    data = await client_from_context(context).get(
        plan_path(plan_id, f"/months/{normalize_month(month)}/categories/{category_id}")
    )
    return shaping.category(data["category"])
