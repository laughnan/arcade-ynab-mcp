"""Shared auth, metadata and parameter helpers for YNAB tools."""

import re
from datetime import date
from typing import Annotated

from arcade_mcp_server.auth import OAuth2
from arcade_mcp_server.exceptions import RetryableToolError
from arcade_mcp_server.metadata import (
    Behavior,
    Classification,
    Operation,
    ServiceDomain,
    ToolMetadata,
)

# Custom OAuth 2.0 provider configured in the Arcade dashboard (see docs/SPEC.md).
# No scopes: YNAB grants full access unless "read-only" is requested.
YNAB_AUTH = OAuth2(id="ynab")

# YNAB has no budgeting domain in Arcade's taxonomy; FINANCIAL_DATA is the closest.
_CLASSIFICATION = Classification(service_domains=[ServiceDomain.FINANCIAL_DATA])

READ_ONLY = ToolMetadata(
    classification=_CLASSIFICATION,
    behavior=Behavior(
        operations=[Operation.READ],
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    ),
)

DEFAULT_PLAN = "last-used"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

PlanId = Annotated[
    str,
    "The YNAB plan (budget) ID. Defaults to 'last-used', the plan the user opened most "
    "recently. Use ListPlans to find other plan IDs.",
]
Month = Annotated[
    str,
    "The plan month: 'current', or a date in the month such as '2026-03' or '2026-03-01'.",
]
Limit = Annotated[int, f"Maximum number of results to return (1-{MAX_LIMIT})."]

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})(?:-\d{2})?$")


def plan_path(plan_id: str, suffix: str = "") -> str:
    return f"/plans/{plan_id or DEFAULT_PLAN}{suffix}"


def normalize_month(value: str) -> str:
    """Return 'current' or the first day of the given month (YYYY-MM-01)."""
    value = (value or "current").strip().lower()
    if value == "current":
        return value
    match = _MONTH_RE.match(value)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1).isoformat()
    raise RetryableToolError(
        f"Invalid month '{value}'.",
        additional_prompt_content="Use 'current' or a month like '2026-03' / '2026-03-01'.",
    )


def validate_date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise RetryableToolError(
            f"Invalid {name} '{value}'.",
            additional_prompt_content=f"Use an ISO date (YYYY-MM-DD) for {name}.",
        ) from None


def clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))
