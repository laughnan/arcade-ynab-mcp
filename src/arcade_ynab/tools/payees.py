"""Payee tools."""

from typing import Annotated

from arcade_mcp_server import Context, tool

from arcade_ynab import shaping
from arcade_ynab.client import client_from_context
from arcade_ynab.tools._common import (
    CREATES,
    DEFAULT_LIMIT,
    DEFAULT_PLAN,
    READ_ONLY,
    UPDATES,
    YNAB_AUTH,
    Limit,
    PlanId,
    clamp_limit,
    path_id,
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
    data = await client_from_context(context).get_list(plan_path(plan_id, "/payees"))
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


@tool(requires_auth=YNAB_AUTH, metadata=CREATES)
async def create_payee(
    context: Context,
    name: Annotated[str, "The payee's name."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The created payee"]:
    """Create a payee. (CreateTransaction can also create a payee from payee_name.)"""
    data = await client_from_context(context).post(
        plan_path(plan_id, "/payees"), {"payee": {"name": name}}
    )
    return shaping.payee(data["payee"])


@tool(requires_auth=YNAB_AUTH, metadata=UPDATES)
async def update_payee(
    context: Context,
    payee_id: Annotated[str, "The payee to rename."],
    name: Annotated[str, "The new name."],
    plan_id: PlanId = DEFAULT_PLAN,
) -> Annotated[dict, "The renamed payee"]:
    """Rename a payee. The new name shows on all of its transactions."""
    data = await client_from_context(context).patch(
        plan_path(plan_id, f"/payees/{path_id(payee_id, 'payee_id')}"), {"payee": {"name": name}}
    )
    return shaping.payee(data["payee"])
