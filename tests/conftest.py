"""Test fixtures: a fake YNAB API served through httpx.MockTransport.

All data here is invented. Tests never call the real API.
"""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from arcade_ynab import client as client_module

API_PREFIX = "/v1"


@dataclass
class FakeYnab:
    routes: dict[tuple[str, str, frozenset | None], tuple[int, Any, dict[str, str]]] = field(
        default_factory=dict
    )
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
        params: dict[str, str] | None = None,
    ) -> None:
        """Register a response. ``data`` is wrapped as ``{"data": ...}``; ``body`` is sent as-is.

        With ``params``, the route only matches requests with exactly those query params;
        otherwise it matches any query string.
        """
        payload = body if body is not None else {"data": data}
        key = (method.upper(), API_PREFIX + path, frozenset(params.items()) if params else None)
        self.routes[key] = (status, payload, headers or {})

    def error(self, method: str, path: str, status: int, error_id: str, detail: str) -> None:
        self.add(
            method,
            path,
            status=status,
            body={"error": {"id": error_id, "name": "error", "detail": detail}},
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        exact = (request.method, request.url.path, frozenset(request.url.params.items()))
        fallback = (request.method, request.url.path, None)
        route = self.routes.get(exact) or self.routes.get(fallback)
        if route is None:
            return httpx.Response(
                404,
                json={"error": {"id": "404.1", "name": "not_found", "detail": f"no route {exact}"}},
            )
        status, payload, headers = route
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
