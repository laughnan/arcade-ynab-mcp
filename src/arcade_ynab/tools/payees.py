"""Payee tools."""

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
async def list_payees(
    context: Context,
    name_contains: Annotated[
        str | None, "Only return payees whose name contains this text (case-insensitive)."
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "Payees sorted by name"]:
    """List a plan's payees, sorted by name. Transfer payees (which represent transfers to
    another account) include the transfer_account_id."""
    data = await client_from_context(context).get(plan_path(plan_id, "/payees"))
    needle = (name_contains or "").strip().lower()
    payees = sorted(
        (
            shaping.payee(p)
            for p in shaping.live(data.get("payees"))
            if needle in (p.get("name") or "").lower()
        ),
        key=lambda p: (p.get("name") or "").lower(),
    )
    kept, info = shaping.truncate(payees, clamp_limit(limit))
    return {"payees": kept, **info}
