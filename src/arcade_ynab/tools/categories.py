"""Category tools."""

from enum import Enum
from typing import Annotated, Any

from arcade_mcp_server import Context, tool
from arcade_mcp_server.exceptions import RetryableToolError, ToolExecutionError, ToolRuntimeError

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.money import from_milliunits, to_milliunits
from arcade_ynab.tools._common import (
    CREATES,
    DEFAULT_PLAN,
    READ_ONLY,
    UPDATES,
    UPDATES_NOT_IDEMPOTENT,
    YNAB_AUTH,
    Month,
    PlanId,
    normalize_month,
    plan_path,
    require_date,
    require_positive,
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
    data = await client_from_context(context).get(plan_path(plan_id, "/categories"))
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


class GoalFrequency(str, Enum):
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    YEARLY = "yearly"


GoalTarget = Annotated[
    float | None,
    "Target amount in currency units. Creates a 'Needed for spending' target if the category "
    "has none.",
]
GoalTargetDate = Annotated[str | None, "Target date (YYYY-MM-DD). Can't combine with frequency."]
GoalFrequencyParam = Annotated[
    GoalFrequency | None,
    "Make the target repeat monthly, weekly or yearly. Requires goal_target.",
]
GoalNeedsWholeAmount = Annotated[
    bool | None,
    "true = 'Set aside another' the target each period; false = 'Refill up to' the target.",
]


def _goal_fields(
    goal_target: float | None,
    goal_target_date: str | None,
    goal_frequency: GoalFrequency | None,
    goal_needs_whole_amount: bool | None,
) -> dict[str, Any]:
    if goal_frequency and goal_target is None:
        raise RetryableToolError(
            "goal_frequency requires goal_target.",
            additional_prompt_content="Pass a goal_target along with goal_frequency.",
        )
    if goal_frequency and goal_target_date:
        raise RetryableToolError(
            "goal_frequency can't be combined with goal_target_date.",
            additional_prompt_content="Use either a repeating target or a target date.",
        )
    fields: dict[str, Any] = {}
    if goal_target is not None:
        fields["goal_target"] = to_milliunits(goal_target)
    if goal_target_date:
        fields["goal_target_date"] = require_date(goal_target_date, "goal_target_date")
    if goal_frequency:
        fields["goal_frequency"] = goal_frequency.value
    if goal_needs_whole_amount is not None:
        fields["goal_needs_whole_amount"] = goal_needs_whole_amount
    return fields


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES)
async def assign_to_category(
    context: Context,
    category_id: Annotated[str, "The category to assign money to."],
    amount: Annotated[
        float,
        "The new TOTAL assigned amount for the month in currency units (not an increment). "
        "Use MoveMoney to move a specific amount between categories.",
    ],
    month: Month = "current",
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The category with its new assigned and available amounts"]:
    """Set how much is assigned to a category for a month. The difference comes from, or
    goes back to, Ready to Assign."""
    data = await client_from_context(context).patch(
        plan_path(plan_id, f"/months/{normalize_month(month)}/categories/{category_id}"),
        {"category": {"budgeted": to_milliunits(amount)}},
    )
    return shaping.category(data["category"])


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES_NOT_IDEMPOTENT)
async def move_money(
    context: Context,
    amount: Annotated[float, "Amount to move, in currency units (positive)."],
    from_category_id: Annotated[
        str | None, "Category to take money from. Omit to take it from Ready to Assign."
    ] = None,
    to_category_id: Annotated[
        str | None, "Category to give money to. Omit to return it to Ready to Assign."
    ] = None,
    month: Month = "current",
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Both categories after the move"]:
    """Move money between two categories, or between a category and Ready to Assign, by
    adjusting their assigned amounts for the month. Running it twice moves the money twice.

    YNAB has no single "move" call, so this updates the source first and then the
    destination. If the second step fails, the money is left in Ready to Assign and the
    error says so."""
    require_positive(amount, "amount")
    if not from_category_id and not to_category_id:
        raise RetryableToolError(
            "Pass from_category_id, to_category_id, or both.",
            additional_prompt_content="Name at least one category.",
        )
    if from_category_id == to_category_id:
        raise RetryableToolError(
            "from_category_id and to_category_id are the same.",
            additional_prompt_content="Pick two different categories.",
        )

    client = client_from_context(context)
    month_key = normalize_month(month)
    data = await client.get(plan_path(plan_id, f"/months/{month_key}"))
    by_id = {c["id"]: c for c in shaping.live(data["month"].get("categories"))}
    for category_id in (from_category_id, to_category_id):
        if category_id and category_id not in by_id:
            raise RetryableToolError(
                f"Category {category_id} isn't in this plan month.",
                additional_prompt_content="Use ListCategories to find valid category IDs.",
            )

    milliunits = to_milliunits(amount)
    result: dict[str, Any] = {"month": data["month"].get("month"), "amount": amount}

    async def set_assigned(category_id: str, budgeted: int) -> dict[str, Any]:
        saved = await client.patch(
            plan_path(plan_id, f"/months/{month_key}/categories/{category_id}"),
            {"category": {"budgeted": budgeted}},
        )
        return shaping.category(saved["category"])

    if from_category_id:
        source = by_id[from_category_id]
        result["from"] = await set_assigned(from_category_id, source["budgeted"] - milliunits)
    else:
        result["from"] = "Ready to Assign"

    if to_category_id:
        target = by_id[to_category_id]
        try:
            result["to"] = await set_assigned(to_category_id, target["budgeted"] + milliunits)
        except ToolRuntimeError as e:
            if not from_category_id:
                raise
            raise ToolExecutionError(
                f"Took {from_milliunits(milliunits)} out of '{source.get('name')}', but adding "
                f"it to '{target.get('name')}' failed ({e.message}). The money is now in Ready "
                "to Assign; use AssignToCategory to finish the move.",
                developer_message=e.developer_message,
            ) from e
    else:
        result["to"] = "Ready to Assign"
    return result


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def create_category(
    context: Context,
    name: Annotated[str, "The new category's name."],
    category_group_id: Annotated[
        str, "The category group to put it in. Use ListCategories to find group IDs."
    ],
    note: Annotated[str | None, "Optional note."] = None,
    goal_target: GoalTarget = None,
    goal_target_date: GoalTargetDate = None,
    goal_frequency: GoalFrequencyParam = None,
    goal_needs_whole_amount: GoalNeedsWholeAmount = None,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The created category"]:
    """Create a category in a category group, optionally with a target (goal)."""
    body: dict[str, Any] = {"name": name, "category_group_id": category_group_id}
    if note:
        body["note"] = note
    body.update(
        _goal_fields(goal_target, goal_target_date, goal_frequency, goal_needs_whole_amount)
    )
    data = await client_from_context(context).post(
        plan_path(plan_id, "/categories"), {"category": body}
    )
    return shaping.category(data["category"])


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES)
async def update_category(
    context: Context,
    category_id: Annotated[str, "The category to change."],
    name: Annotated[str | None, "New name."] = None,
    note: Annotated[str | None, "New note. Pass an empty string to clear it."] = None,
    category_group_id: Annotated[str | None, "Move the category to this group."] = None,
    goal_target: GoalTarget = None,
    goal_target_date: GoalTargetDate = None,
    goal_frequency: GoalFrequencyParam = None,
    goal_needs_whole_amount: GoalNeedsWholeAmount = None,
    remove_goal: Annotated[bool, "Remove the category's target entirely."] = False,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The updated category"]:
    """Rename a category, change its note or group, or set or remove its target (goal).
    Only the fields you pass change. (YNAB's API can't hide or unhide categories.)"""
    if remove_goal and goal_target is not None:
        raise RetryableToolError(
            "Pass either remove_goal or goal_target, not both.",
            additional_prompt_content="Choose whether to remove or change the target.",
        )
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if note is not None:
        body["note"] = note or None
    if category_group_id:
        body["category_group_id"] = category_group_id
    body.update(
        _goal_fields(goal_target, goal_target_date, goal_frequency, goal_needs_whole_amount)
    )
    if remove_goal:
        body["goal_target"] = None
    if not body:
        raise RetryableToolError(
            "No changes were given.",
            additional_prompt_content="Pass at least one field to change.",
        )
    data = await client_from_context(context).patch(
        plan_path(plan_id, f"/categories/{category_id}"), {"category": body}
    )
    return shaping.category(data["category"])


def _category_group_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 50:
        raise RetryableToolError(
            "Category group names must be 1-50 characters.",
            additional_prompt_content="Use a shorter, non-empty name.",
        )
    return name


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def create_category_group(
    context: Context,
    name: Annotated[str, "The new group's name (up to 50 characters)."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The created category group"]:
    """Create a category group."""
    data = await client_from_context(context).post(
        plan_path(plan_id, "/category_groups"),
        {"category_group": {"name": _category_group_name(name)}},
    )
    return shaping.category_group(data["category_group"], include_hidden=True)


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES)
async def update_category_group(
    context: Context,
    category_group_id: Annotated[str, "The category group to rename."],
    name: Annotated[str, "The new name (up to 50 characters)."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The renamed category group"]:
    """Rename a category group."""
    data = await client_from_context(context).patch(
        plan_path(plan_id, f"/category_groups/{category_group_id}"),
        {"category_group": {"name": _category_group_name(name)}},
    )
    return shaping.category_group(data["category_group"], include_hidden=True)
