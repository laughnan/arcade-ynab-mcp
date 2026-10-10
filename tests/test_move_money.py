"""MoveMoney under concurrency and failure: overlapping moves, edits made in YNAB between
the read and the write, a failed second step, and lost responses.

These use a small stateful fake of YNAB's month/category endpoints so that writes change
what later reads return.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from arcade_mcp_server.exceptions import ToolExecutionError, UpstreamError
from conftest import make_category

from arcade_ynab import client as client_module
from arcade_ynab.tools import categories

PREFIX = "/v1/plans/last-used/months/current"

Hook = Callable[["StatefulYnab", httpx.Request], httpx.Response | None]


class StatefulYnab:
    def __init__(self, **budgeted: int) -> None:
        self.budgeted = dict(budgeted)
        self.balance = dict(budgeted)
        self.patches: list[tuple[str, int]] = []
        # Called before a PATCH is applied; may return a response to send instead.
        self.before_patch: Hook | None = None
        # Called after a PATCH is applied; may raise to simulate a lost response.
        self.after_patch: Callable[[str], None] | None = None

    def category(self, category_id: str) -> dict[str, Any]:
        return make_category(
            id=category_id,
            name=category_id.removeprefix("cat-").title(),
            budgeted=self.budgeted[category_id],
            balance=self.balance[category_id],
        )

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == PREFIX:
            cats = [self.category(c) for c in self.budgeted]
            return _ok({"month": {"month": "2026-10-01", "categories": cats}})
        category_id = path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return _ok({"category": self.category(category_id)})
        # Yield so overlapping calls interleave if they aren't serialized.
        await asyncio.sleep(0)
        if self.before_patch:
            response = self.before_patch(self, request)
            if response is not None:
                return response
        new = json.loads(request.content)["category"]["budgeted"]
        self.balance[category_id] += new - self.budgeted[category_id]
        self.budgeted[category_id] = new
        self.patches.append((category_id, new))
        if self.after_patch:
            self.after_patch(category_id)
        return _ok({"category": self.category(category_id)})


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def _server_error() -> httpx.Response:
    return httpx.Response(500, json={"error": {"id": "500", "name": "error", "detail": "boom"}})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> StatefulYnab:
    state = StatefulYnab(**{"cat-fun": 100000, "cat-groceries": 500000})
    monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(state.handler))
    return state


async def move(context, amount, source, target):
    return await categories.move_money(
        context, amount=amount, from_category_id=source, to_category_id=target
    )


async def test_overlapping_moves_both_apply(fake, context):
    await asyncio.gather(
        move(context, 25, "cat-fun", "cat-groceries"),
        move(context, 10, "cat-fun", "cat-groceries"),
    )

    # Unserialized, both calls would read the same totals and the second write would
    # overwrite the first.
    assert fake.budgeted == {"cat-fun": 65000, "cat-groceries": 535000}


async def test_destination_edited_between_read_and_write_is_not_overwritten(fake, context):
    def edit_in_ynab(state: StatefulYnab, request: httpx.Request) -> None:
        if request.url.path.endswith("cat-fun"):
            state.budgeted["cat-groceries"] = 450000  # someone edits Groceries in YNAB
        return None

    fake.before_patch = edit_in_ynab

    with pytest.raises(ToolExecutionError, match="changed while the move was running") as e:
        await move(context, 25, "cat-fun", "cat-groceries")

    assert "now in Ready to Assign" in e.value.message
    assert fake.budgeted["cat-groceries"] == 450000
    assert fake.patches == [("cat-fun", 75000)]


async def test_second_step_failure_leaves_money_in_ready_to_assign(fake, context):
    fake.before_patch = lambda s, r: _server_error() if r.url.path.endswith("groceries") else None

    with pytest.raises(ToolExecutionError, match="now in Ready to Assign") as e:
        await move(context, 25, "cat-fun", "cat-groceries")

    assert "AssignToCategory" in e.value.message
    assert fake.budgeted == {"cat-fun": 75000, "cat-groceries": 500000}


async def test_lost_response_after_successful_write_is_treated_as_success(fake, context):
    def lose_response(category_id: str) -> None:
        if category_id == "cat-groceries":
            raise httpx.ReadTimeout("response lost")

    fake.after_patch = lose_response

    result = await move(context, 25, "cat-fun", "cat-groceries")

    assert result["to"]["assigned"] == 525.0
    assert fake.budgeted == {"cat-fun": 75000, "cat-groceries": 525000}
    assert len(fake.patches) == 2  # no blind retry of the write


async def test_lost_response_on_first_step_is_confirmed_then_continues(fake, context):
    def lose_response(category_id: str) -> None:
        if category_id == "cat-fun":
            raise httpx.ReadTimeout("response lost")

    fake.after_patch = lose_response

    await move(context, 25, "cat-fun", "cat-groceries")

    assert fake.budgeted == {"cat-fun": 75000, "cat-groceries": 525000}


async def test_5xx_that_did_not_apply_on_first_step_changes_nothing(fake, context):
    fake.before_patch = lambda s, r: _server_error()

    with pytest.raises(UpstreamError):
        await move(context, 25, "cat-fun", "cat-groceries")

    assert fake.patches == []


async def test_unconfirmable_outcome_says_not_to_repeat(fake, context, monkeypatch):
    calls = {"n": 0}
    original = fake.handler

    async def flaky(request: httpx.Request) -> httpx.Response:
        # The PATCH to Groceries times out and so does the read used to confirm it.
        if request.url.path.endswith("cat-groceries"):
            calls["n"] += 1
            if calls["n"] > 1:
                raise httpx.ConnectTimeout("unreachable")
        return await original(request)

    monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(flaky))

    with pytest.raises(ToolExecutionError, match="couldn't confirm whether") as e:
        await move(context, 25, "cat-fun", "cat-groceries")

    assert "Don't run MoveMoney again" in e.value.message
    assert "GetCategory" in e.value.message


async def test_moving_from_ready_to_assign_skips_the_extra_read(fake, context, monkeypatch):
    seen: list[str] = []
    original = fake.handler

    async def recording(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path.removeprefix(PREFIX)}")
        return await original(request)

    monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(recording))

    await move(context, 10, None, "cat-groceries")

    assert seen == ["GET ", "PATCH /categories/cat-groceries"]
