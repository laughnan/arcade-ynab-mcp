"""Shared auth, metadata and parameter helpers for YNAB tools."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
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

from arcade_ynab.client import YnabClient

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


def _write(operation: Operation, *, idempotent: bool, destructive: bool = False) -> ToolMetadata:
    return ToolMetadata(
        classification=_CLASSIFICATION,
        behavior=Behavior(
            operations=[operation],
            read_only=False,
            destructive=destructive,
            idempotent=idempotent,
            open_world=True,
        ),
    )


CREATES = _write(Operation.CREATE, idempotent=False)
UPDATES = _write(Operation.UPDATE, idempotent=True)
# Relative changes (e.g. moving money) stack if repeated.
UPDATES_NOT_IDEMPOTENT = _write(Operation.UPDATE, idempotent=False)
DELETES = _write(Operation.DELETE, idempotent=True, destructive=True)


class FlagColor(str, Enum):
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    NONE = "none"


class ClearedStatus(str, Enum):
    CLEARED = "cleared"
    UNCLEARED = "uncleared"
    RECONCILED = "reconciled"


def flag_value(flag: "FlagColor") -> str | None:
    """YNAB clears a flag with null."""
    return None if flag is FlagColor.NONE else flag.value


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


def require_date(value: str, name: str) -> str:
    validated = validate_date(value, name)
    assert validated is not None
    return validated


def require_positive(amount: float, name: str) -> None:
    if amount <= 0:
        raise RetryableToolError(
            f"{name} must be greater than zero.",
            additional_prompt_content=f"Pass a positive {name}.",
        )


@dataclass(frozen=True)
class TransferTarget:
    payee_id: str
    on_budget: bool


async def transfer_target(client: YnabClient, plan_id: str, account_id: str) -> TransferTarget:
    """Look up the payee that represents a transfer into ``account_id``."""
    data = await client.get(plan_path(plan_id, f"/accounts/{account_id}"))
    account = data["account"]
    payee_id = account.get("transfer_payee_id")
    if not payee_id:
        raise RetryableToolError(
            f"Account {account_id} cannot receive transfers.",
            additional_prompt_content="Use ListAccounts to pick a different transfer account.",
        )
    return TransferTarget(payee_id=str(payee_id), on_budget=bool(account.get("on_budget")))


# YNAB requires scheduled transaction dates to be in the future, at most 5 years out.
MAX_SCHEDULE_DAYS = 5 * 365


def is_schedulable(value: str) -> bool:
    today = date.today()
    return today < date.fromisoformat(value) <= today + timedelta(days=MAX_SCHEDULE_DAYS)


def require_schedulable_date(value: str, name: str) -> str:
    validated = require_date(value, name)
    if not is_schedulable(validated):
        raise RetryableToolError(
            f"{name} must be after today and within 5 years.",
            additional_prompt_content=f"Pass a future {name} (YYYY-MM-DD).",
        )
    return validated


def nullable(value: str | None) -> str | None:
    """Map an explicit empty string to None, which YNAB uses to clear a field."""
    return value or None
