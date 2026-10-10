from types import SimpleNamespace

import pytest
from arcade_mcp_server.exceptions import (
    RetryableToolError,
    ToolExecutionError,
    UpstreamError,
    UpstreamRateLimitError,
)

from arcade_ynab import client as client_module
from arcade_ynab.client import YnabClient, client_from_context


async def test_sends_bearer_token_and_returns_data(ynab):
    ynab.add("GET", "/user", {"user": {"id": "user-1"}})

    data = await YnabClient("abc123").get("/user")

    assert data == {"user": {"id": "user-1"}}
    assert ynab.last.headers["Authorization"] == "Bearer abc123"
    assert ynab.last.url.host == "api.ynab.com"


async def test_drops_none_params(ynab):
    ynab.add("GET", "/plans/p/transactions", {"transactions": []})

    await YnabClient("t").get("/plans/p/transactions", since_date="2026-01-01", type=None)

    assert dict(ynab.last.url.params) == {"since_date": "2026-01-01"}


def test_missing_token_raises():
    context = SimpleNamespace(get_auth_token_or_empty=lambda: "")
    with pytest.raises(ToolExecutionError, match="No YNAB authorization"):
        client_from_context(context)


@pytest.mark.parametrize(
    ("status", "error_id", "exc_type", "message"),
    [
        (400, "400", RetryableToolError, "rejected the request: bad date"),
        (401, "401", UpstreamError, "re-authorize"),
        (403, "403.1", UpstreamError, "subscription has lapsed"),
        (403, "403.2", UpstreamError, "trial has expired"),
        (403, "403.3", UpstreamError, "read-only"),
        (404, "404.2", RetryableToolError, "could not find"),
        (500, "500", UpstreamError, "returned an error"),
    ],
)
async def test_error_mapping(ynab, status, error_id, exc_type, message):
    ynab.error("GET", "/user", status, error_id, "bad date")

    with pytest.raises(exc_type, match=message) as raised:
        await YnabClient("t").get("/user")

    assert error_id in (raised.value.developer_message or "")


async def test_rate_limit_uses_retry_after(ynab):
    ynab.add(
        "GET",
        "/user",
        status=429,
        body={"error": {"id": "429", "name": "too_many_requests", "detail": "slow down"}},
        headers={"Retry-After": "120"},
    )

    with pytest.raises(UpstreamRateLimitError) as raised:
        await YnabClient("t").get("/user")

    assert raised.value.retry_after_ms == 120_000


@pytest.mark.parametrize("body", ["Service Unavailable", ["unexpected"], {"error": "oops"}])
async def test_unexpected_error_body(ynab, body):
    ynab.add("GET", "/user", status=503, body=body)

    with pytest.raises(UpstreamError, match=r"\(503\)"):
        await YnabClient("t").get("/user")


async def test_html_error_body(ynab, monkeypatch):
    import httpx

    from arcade_ynab import client as client_module

    monkeypatch.setattr(
        client_module,
        "TRANSPORT",
        httpx.MockTransport(lambda request: httpx.Response(502, text="<html>Bad Gateway</html>")),
    )

    with pytest.raises(UpstreamError, match=r"\(502\)"):
        await YnabClient("t").get("/user")


@pytest.mark.parametrize(
    "bad_id",
    [
        "../transactions/txn-1",
        "..",
        ".",
        "sch-1/../../transactions/txn-1",
        "%2e%2e",
        "%2E%2E%2Ftransactions",
        "sch-1?x=1",
        "sch-1#frag",
        "sch-1\\..\\x",
        "sch 1",
        "",
    ],
)
async def test_rejects_unsafe_path_ids_before_sending(ynab, bad_id):
    with pytest.raises(RetryableToolError, match="Invalid ID"):
        await YnabClient("t").delete(f"/plans/last-used/scheduled_transactions/{bad_id}")

    assert ynab.requests == []


@pytest.mark.parametrize(
    "path",
    [
        "/plans/last-used/scheduled_transactions/0d4f7e3c-1a2b-4c5d-8e9f-0123456789ab",
        "/plans/default/months/2026-10-01/categories/cat-groceries",
        "/plans/last-used/transactions/0d4f7e3c-1a2b-4c5d-8e9f-0123456789ab_2026-11-01",
    ],
)
async def test_accepts_valid_paths(ynab, path):
    ynab.add("GET", path, {"ok": True})

    assert await YnabClient("t").get(path) == {"ok": True}
    assert ynab.last.url.path == "/v1" + path


def test_rejects_relative_path():
    with pytest.raises(ToolExecutionError, match="Invalid YNAB request path"):
        client_module._validate_path("plans/last-used")
