"""Test fixtures: a fake YNAB API served through httpx.MockTransport.

All data here is invented. Tests never call the real API.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from arcade_ynab import client as client_module
from arcade_ynab.tools import _common

TODAY = date(2026, 10, 4)


class FixedDate(date):
    """A ``date`` whose ``today()`` is pinned to TODAY, so tests don't depend on the clock."""

    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


API_PREFIX = "/v1"


@dataclass
class FakeYnab:
    routes: dict[tuple[str, str], tuple[int, Any, dict[str, str]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(
        self,
        method: str,
        path: str,
        data: Any = None,
        *,
        status: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Register a response. ``data`` is wrapped as ``{"data": ...}``; ``body`` is sent as-is."""
        payload = body if body is not None else {"data": data}
        self.routes[(method.upper(), API_PREFIX + path)] = (status, payload, headers or {})

    def error(self, method: str, path: str, status: int, error_id: str, detail: str) -> None:
        self.add(
            method,
            path,
            status=status,
            body={"error": {"id": error_id, "name": "error", "detail": detail}},
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx.Response(
                404,
                json={"error": {"id": "404.1", "name": "not_found", "detail": f"no route {key}"}},
            )
        status, payload, headers = self.routes[key]
        return httpx.Response(status, content=json.dumps(payload), headers=headers)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def last_json(self) -> Any:
        return json.loads(self.last.content)


@pytest.fixture
def ynab(monkeypatch: pytest.MonkeyPatch) -> FakeYnab:
    fake = FakeYnab()
    monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> date:
    monkeypatch.setattr(_common, "date", FixedDate)
    return TODAY


@pytest.fixture
def context() -> SimpleNamespace:
    """Stand-in for arcade_mcp_server.Context carrying a user's OAuth token."""
    return SimpleNamespace(get_auth_token_or_empty=lambda: "test-token")


# --- Sample entities (shapes follow YNAB's OpenAPI schemas) ---


def make_category(**overrides: Any) -> dict[str, Any]:
    category = {
        "id": "cat-groceries",
        "category_group_id": "grp-everyday",
        "category_group_name": "Everyday",
        "name": "Groceries",
        "hidden": False,
        "note": None,
        "budgeted": 500000,
        "activity": -320550,
        "balance": 179450,
        "budgeted_formatted": "$500.00",
        "activity_formatted": "-$320.55",
        "balance_formatted": "$179.45",
        "goal_type": None,
        "deleted": False,
    }
    category.update(overrides)
    return category


def make_account(**overrides: Any) -> dict[str, Any]:
    account = {
        "id": "acct-checking",
        "name": "Checking",
        "type": "checking",
        "on_budget": True,
        "closed": False,
        "note": None,
        "balance": 1234560,
        "cleared_balance": 1200000,
        "uncleared_balance": 34560,
        "balance_formatted": "$1,234.56",
        "transfer_payee_id": "payee-transfer-checking",
        "direct_import_linked": True,
        "direct_import_in_error": False,
        "last_reconciled_at": None,
        "deleted": False,
    }
    account.update(overrides)
    return account


def make_transaction(**overrides: Any) -> dict[str, Any]:
    transaction = {
        "id": "txn-1",
        "date": "2026-09-15",
        "amount": -42500,
        "amount_formatted": "-$42.50",
        "memo": None,
        "cleared": "cleared",
        "approved": True,
        "flag_color": None,
        "flag_name": None,
        "account_id": "acct-checking",
        "account_name": "Checking",
        "payee_id": "payee-market",
        "payee_name": "Corner Market",
        "category_id": "cat-groceries",
        "category_name": "Groceries",
        "transfer_account_id": None,
        "import_payee_name": None,
        "deleted": False,
        "subtransactions": [],
    }
    transaction.update(overrides)
    return transaction
