import pytest
from arcade_mcp_server.exceptions import RetryableToolError
from conftest import make_account, make_category, make_transaction

from arcade_ynab.tools import (
    accounts,
    categories,
    money_movements,
    payees,
    plans,
    scheduled,
    transactions,
)
from arcade_ynab.tools.transactions import TransactionType

PLAN = "/plans/last-used"


# --- plans ---


async def test_get_user(ynab, context):
    ynab.add("GET", "/user", {"user": {"id": "user-1"}})

    assert await plans.get_user(context) == {"id": "user-1"}


async def test_list_plans_sorts_by_last_modified(ynab, context):
    ynab.add(
        "GET",
        "/plans",
        {
            "plans": [
                {"id": "old", "name": "Old", "last_modified_on": "2025-01-01T00:00:00Z"},
                {
                    "id": "new",
                    "name": "New",
                    "last_modified_on": "2026-09-01T00:00:00Z",
                    "currency_format": {"iso_code": "USD"},
                },
            ],
            "default_plan": None,
        },
    )

    result = await plans.list_plans(context)

    assert [p["id"] for p in result["plans"]] == ["new", "old"]
    assert result["plans"][0]["currency"] == "USD"
    assert result["default_plan_id"] is None
    assert ynab.last.url.params["include_accounts"] == "false"


async def test_get_plan_settings_uses_given_plan(ynab, context):
    settings = {"currency_format": {"iso_code": "EUR"}, "date_format": {"format": "DD.MM.YYYY"}}
    ynab.add("GET", "/plans/plan-2/settings", {"settings": settings})

    assert await plans.get_plan_settings(context, plan_id="plan-2") == settings


async def test_get_month_hides_hidden_categories_by_default(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/2026-03-01",
        {
            "month": {
                "month": "2026-03-01",
                "to_be_budgeted": 10000,
                "age_of_money": 45,
                "categories": [
                    make_category(),
                    make_category(id="cat-hidden", name="Old", hidden=True),
                    make_category(id="cat-gone", deleted=True),
                ],
            }
        },
    )

    result = await plans.get_month(context, month="2026-03")

    assert result["ready_to_assign"] == 10.0
    assert result["age_of_money"] == 45
    assert [c["id"] for c in result["categories"]] == ["cat-groceries"]

    result = await plans.get_month(context, month="2026-03-17", include_hidden=True)
    assert [c["id"] for c in result["categories"]] == ["cat-groceries", "cat-hidden"]


async def test_get_month_current(ynab, context):
    ynab.add("GET", f"{PLAN}/months/current", {"month": {"month": "2026-10-01"}})

    result = await plans.get_month(context)

    assert result == {"month": "2026-10-01", "categories": []}


async def test_get_month_rejects_bad_month(ynab, context):
    with pytest.raises(RetryableToolError, match="Invalid month"):
        await plans.get_month(context, month="March")
    assert ynab.requests == []


async def test_list_months_newest_first_with_limit(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months",
        {"months": [{"month": f"2026-0{i}-01"} for i in range(1, 7)], "server_knowledge": 1},
    )

    result = await plans.list_months(context, limit=2)

    assert [m["month"] for m in result["months"]] == ["2026-06-01", "2026-05-01"]
    assert result["total_count"] == 6
    assert result["truncated"] is True


# --- accounts ---


async def test_list_accounts_excludes_closed_by_default(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/accounts",
        {
            "accounts": [make_account(), make_account(id="acct-closed", closed=True)],
            "server_knowledge": 1,
        },
    )

    result = await accounts.list_accounts(context)
    assert [a["id"] for a in result["accounts"]] == ["acct-checking"]

    result = await accounts.list_accounts(context, include_closed=True)
    assert len(result["accounts"]) == 2


async def test_get_account(ynab, context):
    ynab.add("GET", f"{PLAN}/accounts/acct-checking", {"account": make_account()})

    result = await accounts.get_account(context, account_id="acct-checking")

    assert result["balance"] == 1234.56


# --- categories ---


async def test_list_categories_filters_hidden_groups(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/categories",
        {
            "category_groups": [
                {
                    "id": "grp-everyday",
                    "name": "Everyday",
                    "hidden": False,
                    "categories": [make_category(), make_category(id="cat-h", hidden=True)],
                },
                {"id": "grp-hidden", "name": "Hidden", "hidden": True, "categories": []},
            ],
            "server_knowledge": 1,
        },
    )

    result = await categories.list_categories(context)

    assert [g["id"] for g in result["category_groups"]] == ["grp-everyday"]
    assert [c["id"] for c in result["category_groups"][0]["categories"]] == ["cat-groceries"]


async def test_get_category_for_month(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/2026-02-01/categories/cat-groceries",
        {"category": make_category()},
    )

    result = await categories.get_category(context, category_id="cat-groceries", month="2026-02")

    assert result["available"] == 179.45


# --- payees ---


async def test_list_payees_filters_and_sorts(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/payees",
        {
            "payees": [
                {"id": "p2", "name": "Zed's Market"},
                {"id": "p1", "name": "Corner Market"},
                {"id": "p3", "name": "Electric Co"},
                {"id": "p4", "name": "Old Market", "deleted": True},
            ],
            "server_knowledge": 1,
        },
    )

    result = await payees.list_payees(context, name_contains="MARKET")

    assert [p["id"] for p in result["payees"]] == ["p1", "p2"]
    assert result["truncated"] is False


# --- transactions ---


async def test_list_transactions_all_newest_first(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/transactions",
        {
            "transactions": [
                make_transaction(id="a", date="2026-09-01"),
                make_transaction(id="b", date="2026-09-20"),
            ],
            "server_knowledge": 1,
        },
    )

    result = await transactions.list_transactions(
        context,
        since_date="2026-09-01",
        transaction_type=TransactionType.UNAPPROVED,
    )

    assert [t["id"] for t in result["transactions"]] == ["b", "a"]
    assert result["transactions"][0]["amount"] == -42.5
    assert dict(ynab.last.url.params) == {"since_date": "2026-09-01", "type": "unapproved"}


@pytest.mark.parametrize(
    ("kwarg", "path"),
    [
        ("account_id", "/accounts/x/transactions"),
        ("category_id", "/categories/x/transactions"),
        ("payee_id", "/payees/x/transactions"),
    ],
)
async def test_list_transactions_uses_scoped_endpoint(ynab, context, kwarg, path):
    ynab.add("GET", f"{PLAN}{path}", {"transactions": [], "server_knowledge": 1})

    result = await transactions.list_transactions(context, **{kwarg: "x"})

    assert result["transactions"] == []
    assert ynab.last.url.path == f"/v1{PLAN}{path}"


async def test_list_transactions_rejects_multiple_scopes(ynab, context):
    with pytest.raises(RetryableToolError, match="only one"):
        await transactions.list_transactions(context, account_id="a", payee_id="p")
    assert ynab.requests == []


async def test_list_transactions_rejects_bad_date(ynab, context):
    with pytest.raises(RetryableToolError, match="since_date"):
        await transactions.list_transactions(context, since_date="09/01/2026")


async def test_list_transactions_hybrid_rows(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/categories/cat-groceries/transactions",
        {
            "transactions": [
                make_transaction(
                    id="sub-1",
                    type="subtransaction",
                    parent_transaction_id="txn-9",
                    amount=-10000,
                    subtransactions=None,
                )
            ]
        },
    )

    result = await transactions.list_transactions(context, category_id="cat-groceries")

    row = result["transactions"][0]
    assert row["type"] == "subtransaction"
    assert row["parent_transaction_id"] == "txn-9"
    assert row["amount"] == -10.0


async def test_get_transaction(ynab, context):
    ynab.add("GET", f"{PLAN}/transactions/txn-1", {"transaction": make_transaction()})

    result = await transactions.get_transaction(context, transaction_id="txn-1")

    assert result["payee_name"] == "Corner Market"


# --- scheduled transactions ---


async def test_list_scheduled_transactions_soonest_first(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/scheduled_transactions",
        {
            "scheduled_transactions": [
                {
                    "id": "rent",
                    "date_next": "2026-11-01",
                    "frequency": "monthly",
                    "amount": -1500000,
                },
                {"id": "gym", "date_next": "2026-10-10", "frequency": "monthly", "amount": -40000},
            ],
            "server_knowledge": 1,
        },
    )

    result = await scheduled.list_scheduled_transactions(context)

    assert [t["id"] for t in result["scheduled_transactions"]] == ["gym", "rent"]
    assert result["scheduled_transactions"][1]["amount"] == -1500.0


# --- money movements ---


async def test_list_money_movements_for_month(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/2026-09-01/money_movements",
        {
            "money_movements": [
                {
                    "id": "m1",
                    "moved_at": "2026-09-02T10:00:00Z",
                    "amount": 50000,
                    "from_category_id": None,
                    "to_category_id": "cat-groceries",
                    "money_movement_group_id": "g1",
                },
                {
                    "id": "m2",
                    "moved_at": "2026-09-05T10:00:00Z",
                    "amount": 20000,
                    "from_category_id": "cat-fun",
                    "to_category_id": "cat-groceries",
                    "money_movement_group_id": "g2",
                },
            ],
            "server_knowledge": 1,
        },
    )
    ynab.add(
        "GET",
        f"{PLAN}/months/2026-09-01/money_movement_groups",
        {
            "money_movement_groups": [
                {"id": "g1", "month": "2026-09-01", "group_created_at": "2026-09-02T10:00:00Z"},
                {"id": "g2", "month": "2026-09-01", "group_created_at": "2026-09-05T10:00:00Z"},
            ],
            "server_knowledge": 1,
        },
    )

    result = await money_movements.list_money_movements(context, month="2026-09", limit=1)

    assert [m["id"] for m in result["money_movements"]] == ["m2"]
    assert result["money_movements"][0]["amount"] == 20.0
    assert [g["id"] for g in result["money_movement_groups"]] == ["g2"]
    assert result["truncated"] is True


async def test_list_money_movements_all_months(ynab, context):
    ynab.add("GET", f"{PLAN}/money_movements", {"money_movements": [], "server_knowledge": 1})
    ynab.add(
        "GET", f"{PLAN}/money_movement_groups", {"money_movement_groups": [], "server_knowledge": 1}
    )

    result = await money_movements.list_money_movements(context)

    assert result["money_movements"] == []
    assert result["total_count"] == 0
