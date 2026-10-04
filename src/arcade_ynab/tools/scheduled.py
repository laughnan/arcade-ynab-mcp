"""Scheduled transaction tools."""

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
    plan_path,
)


@tool(requires_auth=YNAB_AUTH, metadata=READ_ONLY)
async def list_scheduled_transactions(
    context: Context,
    limit: Limit = DEFAULT_LIMIT,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Scheduled transactions, soonest first"]:
    """List upcoming and recurring scheduled transactions, soonest first, with their
    frequency and next date. Amounts are negative for outflows, positive for inflows."""
    data = await client_from_context(context).get_list(
        plan_path(plan_id, "/scheduled_transactions")
    )
    scheduled = sorted(
        (
            shaping.scheduled_transaction(t)
            for t in shaping.live(data.get("scheduled_transactions"))
        ),
        key=lambda t: t.get("date_next") or "",
    )
    kept, info = shaping.truncate(scheduled, clamp_limit(limit))
    return {"scheduled_transactions": kept, **info}
