"""Money movement tools."""

from typing import Annotated

from arcade_mcp_server import Context, tool

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
    normalize_month,
    plan_path,
)


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_money_movements(
    context: Context,
    month: Annotated[
        str | None,
        "Only include movements in this plan month: 'current', '2026-03' or '2026-03-01'. "
        "Omit for all months.",
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Money movements, newest first, and the groups they belong to"]:
    """List money moved between categories, or between a category and Ready to Assign,
    newest first. Movements made together in one action share a money_movement_group_id.

    A missing from_category_id means the money came from Ready to Assign; a missing
    to_category_id means it went to Ready to Assign. Use ListCategories to resolve IDs."""
    client = client_from_context(context)
    prefix = f"/months/{normalize_month(month)}" if month else ""
    movements_data = await client.get_list(plan_path(plan_id, f"{prefix}/money_movements"))
    groups_data = await client.get_list(plan_path(plan_id, f"{prefix}/money_movement_groups"))

    movements = sorted(
        (shaping.money_movement(m) for m in movements_data.get("money_movements") or []),
        key=lambda m: m.get("moved_at") or m.get("month") or "",
        reverse=True,
    )
    kept, info = shaping.truncate(movements, clamp_limit(limit))
    group_ids = {m.get("money_movement_group_id") for m in kept}
    groups = [
        shaping.money_movement_group(g)
        for g in groups_data.get("money_movement_groups") or []
        if g.get("id") in group_ids
    ]
    return {"money_movements": kept, "money_movement_groups": groups, **info}
