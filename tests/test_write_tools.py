import json

import pytest
from arcade_mcp_server.exceptions import RetryableToolError, ToolExecutionError
from conftest import make_account, make_category, make_transaction

from arcade_ynab.tools import accounts, categories, payees, scheduled, transactions
from arcade_ynab.tools._common import ClearedStatus, FlagColor
from arcade_ynab.tools.accounts import NewAccountType
from arcade_ynab.tools.categories import GoalFrequency
from arcade_ynab.tools.scheduled import Frequency

PLAN = "/plans/last-used"


def bodies(ynab, method):
    return [json.loads(r.content) for r in ynab.requests if r.method == method]


# --- CreateTransaction ---


async def test_create_transaction_defaults(ynab, context):
    ynab.add("POST", f"{PLAN}/transactions", {"transaction": make_transaction()})

    result = await transactions.create_transaction(
        context,
        account_id="acct-checking",
        date="2026-09-15",
        amount=-42.5,
        payee_name="Corner Market",
        category_id="cat-groceries",
    )

    assert ynab.last_json() == {
        "transaction": {
            "account_id": "acct-checking",
            "date": "2026-09-15",
            "amount": -42500,
            "cleared": "uncleared",
            "approved": True,
            "payee_name": "Corner Market",
            "category_id": "cat-groceries",
        }
    }
    assert result["amount"] == -42.5


async def test_create_transaction_transfer_looks_up_transfer_payee(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/accounts/acct-savings",
        {"account": make_account(id="acct-savings", transfer_payee_id="payee-to-savings")},
    )
    ynab.add("POST", f"{PLAN}/transactions", {"transaction": make_transaction()})

    await transactions.create_transaction(
        context,
        account_id="acct-checking",
        date="2026-09-15",
        amount=-100,
        transfer_account_id="acct-savings",
        cleared=ClearedStatus.CLEARED,
        flag_color=FlagColor.BLUE,
    )

    body = ynab.last_json()["transaction"]
    assert body["payee_id"] == "payee-to-savings"
    assert body["cleared"] == "cleared"
    assert body["flag_color"] == "blue"
    assert "payee_name" not in body


async def test_create_transaction_rejects_transfer_with_payee(ynab, context):
    with pytest.raises(RetryableToolError, match="transfer"):
        await transactions.create_transaction(
            context,
            account_id="a",
            date="2026-09-15",
            amount=-1,
            transfer_account_id="b",
            payee_name="Someone",
        )
    assert ynab.requests == []


async def test_create_split_transaction(ynab, context):
    ynab.add("POST", f"{PLAN}/transactions", {"transaction": make_transaction()})

    await transactions.create_transaction(
        context,
        account_id="acct-checking",
        date="2026-09-15",
        amount=-42.5,
        payee_name="Big Box Store",
        subtransactions=[
            {"amount": -30, "category_id": "cat-groceries"},
            {"amount": -12.5, "category_id": "cat-household", "memo": "soap"},
        ],
    )

    assert ynab.last_json()["transaction"]["subtransactions"] == [
        {"amount": -30000, "category_id": "cat-groceries"},
        {"amount": -12500, "category_id": "cat-household", "memo": "soap"},
    ]


async def test_create_split_transaction_must_add_up(ynab, context):
    with pytest.raises(RetryableToolError, match="add up to -40.0"):
        await transactions.create_transaction(
            context,
            account_id="a",
            date="2026-09-15",
            amount=-42.5,
            subtransactions=[{"amount": -30}, {"amount": -10}],
        )


async def test_create_split_transaction_rejects_category(ynab, context):
    with pytest.raises(RetryableToolError, match="split"):
        await transactions.create_transaction(
            context,
            account_id="a",
            date="2026-09-15",
            amount=-10,
            category_id="c",
            subtransactions=[{"amount": -10}],
        )


async def test_create_transaction_rejects_bad_date(ynab, context):
    with pytest.raises(RetryableToolError, match="date"):
        await transactions.create_transaction(context, account_id="a", date="yesterday", amount=-1)


# --- UpdateTransactions ---


async def test_update_transactions_bulk(ynab, context):
    ynab.add(
        "PATCH",
        f"{PLAN}/transactions",
        {"transactions": [make_transaction(id="t1"), make_transaction(id="t2")]},
    )

    result = await transactions.update_transactions(
        context,
        transaction_ids=["t1", "t2", "t1"],
        approved=True,
        category_id="cat-groceries",
        flag_color=FlagColor.NONE,
        memo="",
    )

    assert ynab.last_json() == {
        "transactions": [
            {
                "id": "t1",
                "approved": True,
                "category_id": "cat-groceries",
                "memo": None,
                "flag_color": None,
            },
            {
                "id": "t2",
                "approved": True,
                "category_id": "cat-groceries",
                "memo": None,
                "flag_color": None,
            },
        ]
    }
    assert result["updated_count"] == 2


async def test_update_transactions_amount_single_only(ynab, context):
    with pytest.raises(RetryableToolError, match="single"):
        await transactions.update_transactions(context, transaction_ids=["a", "b"], amount=-5)

    ynab.add("PATCH", f"{PLAN}/transactions", {"transactions": [make_transaction()]})
    await transactions.update_transactions(context, transaction_ids=["a"], amount=-5)
    assert ynab.last_json()["transactions"][0]["amount"] == -5000


async def test_update_transactions_requires_changes(ynab, context):
    with pytest.raises(RetryableToolError, match="No changes"):
        await transactions.update_transactions(context, transaction_ids=["a"])


async def test_update_transactions_limits_batch_size(ynab, context):
    with pytest.raises(RetryableToolError, match="between 1 and 100"):
        await transactions.update_transactions(
            context, transaction_ids=[f"t{i}" for i in range(101)], approved=True
        )
    with pytest.raises(RetryableToolError, match="between 1 and 100"):
        await transactions.update_transactions(context, transaction_ids=[], approved=True)


# --- DeleteTransaction / ImportTransactions ---


async def test_delete_transaction(ynab, context):
    ynab.add("DELETE", f"{PLAN}/transactions/txn-1", {"transaction": make_transaction()})

    result = await transactions.delete_transaction(context, transaction_id="txn-1")

    assert result["deleted"] is True
    assert result["transaction"]["id"] == "txn-1"


async def test_import_transactions(ynab, context):
    ynab.add("POST", f"{PLAN}/transactions/import", {"transaction_ids": ["a", "b"]})

    result = await transactions.import_transactions(context)

    assert result == {"imported_count": 2, "transaction_ids": ["a", "b"]}


# --- AssignToCategory / MoveMoney ---


async def test_assign_to_category_sets_total(ynab, context):
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=600000)},
    )

    result = await categories.assign_to_category(context, category_id="cat-groceries", amount=600)

    assert ynab.last_json() == {"category": {"budgeted": 600000}}
    assert result["assigned"] == 600.0


def _month_with(*cats):
    return {"month": {"month": "2026-10-01", "categories": list(cats)}}


async def test_move_money_between_categories(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/current",
        _month_with(
            make_category(id="cat-fun", name="Fun", budgeted=100000),
            make_category(id="cat-groceries", budgeted=500000),
        ),
    )
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-fun",
        {"category": make_category(id="cat-fun", budgeted=75000)},
    )
    ynab.add(
        "GET",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=500000)},
    )
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=525000)},
    )

    result = await move_money(context, 25, "cat-fun", "cat-groceries")

    assert bodies(ynab, "PATCH") == [
        {"category": {"budgeted": 75000}},
        {"category": {"budgeted": 525000}},
    ]
    assert result["from"]["assigned"] == 75.0
    assert result["to"]["assigned"] == 525.0


async def move_money(context, amount, source, target):
    return await categories.move_money(
        context, amount=amount, from_category_id=source, to_category_id=target
    )


async def test_move_money_from_ready_to_assign(ynab, context):
    ynab.add("GET", f"{PLAN}/months/current", _month_with(make_category(budgeted=500000)))
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=510000)},
    )

    result = await move_money(context, 10, None, "cat-groceries")

    assert bodies(ynab, "PATCH") == [{"category": {"budgeted": 510000}}]
    assert result["from"] == "Ready to Assign"


async def test_move_money_partial_failure_explains_state(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/current",
        _month_with(
            make_category(id="cat-fun", name="Fun", budgeted=100000),
            make_category(id="cat-groceries", budgeted=500000),
        ),
    )
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-fun",
        {"category": make_category(id="cat-fun", budgeted=75000)},
    )
    ynab.add(
        "GET",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=500000)},
    )
    ynab.error(
        "PATCH", f"{PLAN}/months/current/categories/cat-groceries", 500, "500", "server error"
    )

    with pytest.raises(ToolExecutionError, match="now in Ready to Assign"):
        await move_money(context, 25, "cat-fun", "cat-groceries")


@pytest.mark.parametrize(
    ("amount", "source", "target", "message"),
    [
        (0, "cat-fun", "cat-groceries", "greater than zero"),
        (5, None, None, "Pass from_category_id"),
        (5, "cat-fun", "cat-fun", "the same"),
        (5, "cat-missing", "cat-groceries", "isn't in this plan month"),
    ],
)
async def test_move_money_validation(ynab, context, amount, source, target, message):
    ynab.add("GET", f"{PLAN}/months/current", _month_with(make_category()))

    with pytest.raises(RetryableToolError, match=message):
        await move_money(context, amount, source, target)
    assert bodies(ynab, "PATCH") == []


# --- Categories and groups ---


async def test_create_category_with_repeating_goal(ynab, context):
    ynab.add("POST", f"{PLAN}/categories", {"category": make_category(), "server_knowledge": 1})

    await categories.create_category(
        context,
        name="Gym",
        category_group_id="grp-everyday",
        goal_target=40,
        goal_frequency=GoalFrequency.MONTHLY,
        goal_needs_whole_amount=True,
    )

    assert ynab.last_json() == {
        "category": {
            "name": "Gym",
            "category_group_id": "grp-everyday",
            "goal_target": 40000,
            "goal_frequency": "monthly",
            "goal_needs_whole_amount": True,
        }
    }


async def test_goal_frequency_needs_target(ynab, context):
    with pytest.raises(RetryableToolError, match="requires goal_target"):
        await categories.create_category(
            context, name="Gym", category_group_id="g", goal_frequency=GoalFrequency.WEEKLY
        )


async def test_update_category_remove_goal_and_clear_note(ynab, context):
    ynab.add(
        "PATCH",
        f"{PLAN}/categories/cat-groceries",
        {"category": make_category(), "server_knowledge": 1},
    )

    await categories.update_category(
        context, category_id="cat-groceries", note="", remove_goal=True
    )

    assert ynab.last_json() == {"category": {"note": None, "goal_target": None}}


async def test_update_category_requires_changes(ynab, context):
    with pytest.raises(RetryableToolError, match="No changes"):
        await categories.update_category(context, category_id="cat-groceries")


async def test_category_group_create_and_rename(ynab, context):
    group = {"id": "grp-new", "name": "Travel", "hidden": False, "deleted": False}
    ynab.add("POST", f"{PLAN}/category_groups", {"category_group": group, "server_knowledge": 1})
    ynab.add(
        "PATCH",
        f"{PLAN}/category_groups/grp-new",
        {"category_group": {**group, "name": "Trips"}, "server_knowledge": 2},
    )

    created = await categories.create_category_group(context, name="  Travel ")
    renamed = await categories.update_category_group(
        context, category_group_id="grp-new", name="Trips"
    )

    assert created == {"id": "grp-new", "name": "Travel"}
    assert bodies(ynab, "POST") == [{"category_group": {"name": "Travel"}}]
    assert renamed["name"] == "Trips"


async def test_category_group_name_length(ynab, context):
    with pytest.raises(RetryableToolError, match="1-50"):
        await categories.create_category_group(context, name="x" * 51)


# --- Payees and accounts ---


async def test_create_and_update_payee(ynab, context):
    ynab.add("POST", f"{PLAN}/payees", {"payee": {"id": "p1", "name": "Bakery"}})
    ynab.add("PATCH", f"{PLAN}/payees/p1", {"payee": {"id": "p1", "name": "Corner Bakery"}})

    assert await payees.create_payee(context, name="Bakery") == {"id": "p1", "name": "Bakery"}
    result = await payees.update_payee(context, payee_id="p1", name="Corner Bakery")

    assert ynab.last_json() == {"payee": {"name": "Corner Bakery"}}
    assert result["name"] == "Corner Bakery"


async def test_create_account(ynab, context):
    ynab.add("POST", f"{PLAN}/accounts", {"account": make_account(type="creditCard")})

    await accounts.create_account(
        context, name="Visa", account_type=NewAccountType.CREDIT_CARD, balance=-250.75
    )

    assert ynab.last_json() == {
        "account": {"name": "Visa", "type": "creditCard", "balance": -250750}
    }


# --- Scheduled transactions ---

SCHEDULED = {
    "id": "sch-rent",
    "date_first": "2026-01-01",
    "date_next": "2026-11-01",
    "frequency": "monthly",
    "amount": -1500000,
    "memo": "rent",
    "flag_color": None,
    "account_id": "acct-checking",
    "payee_id": "payee-landlord",
    "category_id": "cat-rent",
    "deleted": False,
}


async def test_create_scheduled_transaction(ynab, context):
    ynab.add("POST", f"{PLAN}/scheduled_transactions", {"scheduled_transaction": SCHEDULED})

    result = await scheduled.create_scheduled_transaction(
        context,
        account_id="acct-checking",
        date="2026-11-01",
        amount=-1500,
        frequency=Frequency.MONTHLY,
        payee_name="Landlord",
        category_id="cat-rent",
        memo="rent",
    )

    assert ynab.last_json() == {
        "scheduled_transaction": {
            "account_id": "acct-checking",
            "date": "2026-11-01",
            "amount": -1500000,
            "frequency": "monthly",
            "payee_id": None,
            "payee_name": "Landlord",
            "category_id": "cat-rent",
            "memo": "rent",
        }
    }
    assert result["amount"] == -1500.0


async def test_update_scheduled_transaction_merges_current_values(ynab, context):
    path = f"{PLAN}/scheduled_transactions/sch-rent"
    ynab.add("GET", path, {"scheduled_transaction": SCHEDULED})
    ynab.add("PUT", path, {"scheduled_transaction": {**SCHEDULED, "amount": -1550000}})

    result = await scheduled.update_scheduled_transaction(
        context, scheduled_transaction_id="sch-rent", amount=-1550, memo=""
    )

    assert ynab.last_json() == {
        "scheduled_transaction": {
            "account_id": "acct-checking",
            "date": "2026-11-01",
            "amount": -1550000,
            "frequency": "monthly",
            "payee_id": "payee-landlord",
            "category_id": "cat-rent",
            "memo": None,
        }
    }
    assert result["amount"] == -1550.0


async def test_update_scheduled_transaction_switch_to_payee_name(ynab, context):
    path = f"{PLAN}/scheduled_transactions/sch-rent"
    ynab.add("GET", path, {"scheduled_transaction": SCHEDULED})
    ynab.add("PUT", path, {"scheduled_transaction": SCHEDULED})

    await scheduled.update_scheduled_transaction(
        context, scheduled_transaction_id="sch-rent", payee_name="New Landlord"
    )

    body = ynab.last_json()["scheduled_transaction"]
    # YNAB only uses payee_name when payee_id is null, so it must be sent as null.
    assert body["payee_name"] == "New Landlord"
    assert body["payee_id"] is None


async def test_delete_scheduled_transaction(ynab, context):
    ynab.add(
        "DELETE",
        f"{PLAN}/scheduled_transactions/sch-rent",
        {"scheduled_transaction": SCHEDULED},
    )

    result = await scheduled.delete_scheduled_transaction(
        context, scheduled_transaction_id="sch-rent"
    )

    assert result["deleted"] is True
    assert result["scheduled_transaction"]["id"] == "sch-rent"


# --- review follow-ups ---


async def test_update_transactions_can_clear_category_and_payee(ynab, context):
    ynab.add("PATCH", f"{PLAN}/transactions", {"transactions": [make_transaction()]})

    await transactions.update_transactions(
        context, transaction_ids=["t1"], category_id="", payee_id=""
    )

    assert ynab.last_json() == {
        "transactions": [{"id": "t1", "category_id": None, "payee_id": None}]
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"subtransactions": [{"amount": -60}, {"amount": -40}]},
        {"category_id": "cat-groceries"},
    ],
)
async def test_on_budget_transfer_rejects_split_or_category(ynab, context, extra):
    ynab.add(
        "GET",
        f"{PLAN}/accounts/acct-savings",
        {"account": make_account(id="acct-savings", on_budget=True)},
    )

    with pytest.raises(RetryableToolError, match="two budget accounts"):
        await transactions.create_transaction(
            context,
            account_id="acct-checking",
            date="2026-10-01",
            amount=-100,
            transfer_account_id="acct-savings",
            **extra,
        )
    assert bodies(ynab, "POST") == []


async def test_transfer_to_tracking_account_can_be_split(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/accounts/acct-loan",
        {"account": make_account(id="acct-loan", on_budget=False, transfer_payee_id="p-loan")},
    )
    ynab.add("POST", f"{PLAN}/transactions", {"transaction": make_transaction()})

    await transactions.create_transaction(
        context,
        account_id="acct-checking",
        date="2026-10-01",
        amount=-100,
        transfer_account_id="acct-loan",
        subtransactions=[
            {"amount": -80, "category_id": "cat-loan"},
            {"amount": -20, "category_id": "cat-interest"},
        ],
    )

    assert ynab.last_json()["transaction"]["payee_id"] == "p-loan"


async def test_move_money_checks_source_available(ynab, context):
    # Assigned only $20 but $100 available (rolled over): moving $100 is allowed.
    ynab.add(
        "GET",
        f"{PLAN}/months/current",
        _month_with(
            make_category(id="cat-fun", name="Fun", budgeted=20000, balance=100000),
            make_category(id="cat-groceries", budgeted=500000),
        ),
    )
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-fun",
        {"category": make_category(id="cat-fun", budgeted=-80000, balance=0)},
    )
    ynab.add(
        "GET",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=500000)},
    )
    ynab.add(
        "PATCH",
        f"{PLAN}/months/current/categories/cat-groceries",
        {"category": make_category(budgeted=600000)},
    )

    await move_money(context, 100, "cat-fun", "cat-groceries")
    assert bodies(ynab, "PATCH")[0] == {"category": {"budgeted": -80000}}

    with pytest.raises(RetryableToolError, match="only has 100.0 available"):
        await move_money(context, 100.01, "cat-fun", "cat-groceries")


async def test_update_scheduled_transaction_requires_changes(ynab, context):
    with pytest.raises(RetryableToolError, match="No changes"):
        await scheduled.update_scheduled_transaction(context, scheduled_transaction_id="x")
    assert ynab.requests == []


async def test_update_scheduled_transaction_when_next_date_is_today(ynab, context):
    path = f"{PLAN}/scheduled_transactions/sch-rent"
    ynab.add("GET", path, {"scheduled_transaction": {**SCHEDULED, "date_next": "2026-10-04"}})

    with pytest.raises(RetryableToolError, match="isn't in the future"):
        await scheduled.update_scheduled_transaction(
            context, scheduled_transaction_id="sch-rent", amount=-1550
        )
    assert bodies(ynab, "PUT") == []

    ynab.add("PUT", path, {"scheduled_transaction": SCHEDULED})
    await scheduled.update_scheduled_transaction(
        context, scheduled_transaction_id="sch-rent", amount=-1550, date="2026-11-04"
    )
    assert ynab.last_json()["scheduled_transaction"]["date"] == "2026-11-04"


@pytest.mark.parametrize("bad_date", ["2026-10-04", "2026-09-30", "2031-10-10"])
async def test_scheduled_dates_must_be_future_within_5_years(ynab, context, bad_date):
    with pytest.raises(RetryableToolError, match="after today and within 5 years"):
        await scheduled.create_scheduled_transaction(
            context,
            account_id="a",
            date=bad_date,
            amount=-1,
            frequency=Frequency.MONTHLY,
        )


async def test_scheduled_on_budget_transfer_clears_category(ynab, context):
    path = f"{PLAN}/scheduled_transactions/sch-rent"
    ynab.add("GET", path, {"scheduled_transaction": SCHEDULED})
    ynab.add(
        "GET",
        f"{PLAN}/accounts/acct-savings",
        {"account": make_account(id="acct-savings", transfer_payee_id="p-savings")},
    )
    ynab.add("PUT", path, {"scheduled_transaction": SCHEDULED})

    await scheduled.update_scheduled_transaction(
        context, scheduled_transaction_id="sch-rent", transfer_account_id="acct-savings"
    )

    body = ynab.last_json()["scheduled_transaction"]
    assert body["payee_id"] == "p-savings"
    assert body["category_id"] is None


async def test_scheduled_clear_category(ynab, context):
    path = f"{PLAN}/scheduled_transactions/sch-rent"
    ynab.add("GET", path, {"scheduled_transaction": SCHEDULED})
    ynab.add("PUT", path, {"scheduled_transaction": SCHEDULED})

    await scheduled.update_scheduled_transaction(
        context, scheduled_transaction_id="sch-rent", category_id=""
    )

    assert ynab.last_json()["scheduled_transaction"]["category_id"] is None


async def test_update_transactions_payee_name_sends_null_payee_id(ynab, context):
    ynab.add("PATCH", f"{PLAN}/transactions", {"transactions": [make_transaction()]})

    await transactions.update_transactions(context, transaction_ids=["t1"], payee_name="Bakery")

    assert ynab.last_json() == {
        "transactions": [{"id": "t1", "payee_id": None, "payee_name": "Bakery"}]
    }
