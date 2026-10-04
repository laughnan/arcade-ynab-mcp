from datetime import date

import pytest
from arcade_mcp_server.exceptions import RetryableToolError
from conftest import make_account, make_category, make_transaction

from arcade_ynab.tools import insights
from arcade_ynab.tools.insights import GroupBy, occurrences

PLAN = "/plans/last-used"
TODAY = date(2026, 10, 4)


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(insights, "date", FixedDate)


# --- ReviewUnapproved ---


async def test_review_unapproved_suggests_most_common_category(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/transactions",
        {
            "transactions": [
                make_transaction(id="u1", approved=False, category_id=None, category_name=None),
                make_transaction(
                    id="u2",
                    approved=False,
                    payee_id="payee-new",
                    payee_name="New Place",
                    category_id=None,
                    category_name=None,
                ),
            ]
        },
        params={"type": "unapproved"},
    )
    ynab.add(
        "GET",
        f"{PLAN}/transactions",
        {
            "transactions": [
                make_transaction(id="u1", approved=False, category_id=None, category_name=None),
                make_transaction(id="h1", category_id="cat-groceries", category_name="Groceries"),
                make_transaction(id="h2", category_id="cat-groceries", category_name="Groceries"),
                make_transaction(id="h3", category_id="cat-dining", category_name="Dining"),
                make_transaction(id="h4", transfer_account_id="acct-savings", category_id="cat-x"),
            ]
        },
        params={"since_date": "2026-09-04"},
    )

    result = await insights.review_unapproved(context, history_days=30)

    params = [dict(r.url.params) for r in ynab.requests]
    assert params == [{"type": "unapproved"}, {"since_date": "2026-09-04"}]
    by_id = {t["id"]: t for t in result["unapproved"]}
    assert set(by_id) == {"u1", "u2"}
    assert "suggested_category" not in by_id["u2"]
    assert by_id["u1"]["suggested_category"] == {
        "id": "cat-groceries",
        "name": "Groceries",
        "confidence": 0.67,
        "matches_current": False,
    }
    assert result["uncategorized_count"] == 2


# --- FindOverspending ---


async def test_find_overspending(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/current",
        {
            "month": {
                "month": "2026-10-01",
                "to_be_budgeted": 20000,
                "categories": [
                    make_category(id="dining", name="Dining", balance=-30000),
                    make_category(id="gas", name="Gas", balance=-5000),
                    make_category(id="fun", name="Fun", balance=80000),
                    make_category(
                        id="vacation",
                        name="Vacation",
                        balance=200000,
                        goal_type="TB",
                        goal_under_funded=50000,
                    ),
                    make_category(
                        id="visa",
                        name="Visa",
                        balance=300000,
                        category_group_name="Credit Card Payments",
                    ),
                    make_category(id="old", name="Old", balance=900000, hidden=True),
                    make_category(id="groceries", name="Groceries", balance=40000),
                ],
            }
        },
    )

    result = await insights.find_overspending(context)

    assert result["total_overspent"] == 35.0
    assert result["ready_to_assign"] == 20.0
    assert result["ready_to_assign_covers_all"] is False
    assert [c["category_id"] for c in result["overspent"]] == ["dining", "gas"]
    assert result["overspent"][0]["overspent_by"] == 30.0
    assert [c["category_id"] for c in result["candidates"]] == ["fun", "groceries"]


# --- SummarizeSpending ---


def _seed_spending(ynab):
    ynab.add(
        "GET",
        f"{PLAN}/transactions",
        {
            "transactions": [
                make_transaction(id="t1", amount=-50000),
                make_transaction(
                    id="t2",
                    amount=-25000,
                    payee_id="payee-cafe",
                    payee_name="Cafe",
                    category_id="cat-dining",
                    category_name="Dining",
                ),
                make_transaction(id="refund", amount=10000),
                make_transaction(
                    id="split",
                    amount=-40000,
                    category_id=None,
                    category_name="Split",
                    subtransactions=[
                        {
                            "id": "s1",
                            "amount": -30000,
                            "category_id": "cat-groceries",
                            "category_name": "Groceries",
                            "deleted": False,
                        },
                        {
                            "id": "s2",
                            "amount": -10000,
                            "category_id": "cat-dining",
                            "category_name": "Dining",
                            "deleted": False,
                        },
                    ],
                ),
                make_transaction(
                    id="pay",
                    amount=3000000,
                    payee_name="Employer",
                    category_id="cat-rta",
                    category_name="Inflow: Ready to Assign",
                ),
                make_transaction(
                    id="xfer",
                    amount=-100000,
                    transfer_account_id="acct-savings",
                    category_id=None,
                    category_name=None,
                ),
                make_transaction(id="tracking", account_id="acct-brokerage", amount=-999000),
            ]
        },
    )
    ynab.add(
        "GET",
        f"{PLAN}/accounts",
        {
            "accounts": [
                make_account(),
                make_account(id="acct-brokerage", on_budget=False, type="otherAsset"),
            ]
        },
    )


async def test_summarize_spending_by_category(ynab, context):
    _seed_spending(ynab)

    result = await insights.summarize_spending(
        context, since_date="2026-09-01", until_date="2026-09-30"
    )

    assert ynab.requests[0].url.params["until_date"] == "2026-09-30"
    assert result["total_spent"] == 105.0
    assert result["groups"] == [
        {"id": "cat-groceries", "name": "Groceries", "spent": 70.0, "transaction_count": 3},
        {"id": "cat-dining", "name": "Dining", "spent": 35.0, "transaction_count": 2},
    ]


async def test_summarize_spending_by_payee_defaults_until_today(ynab, context):
    _seed_spending(ynab)

    result = await insights.summarize_spending(
        context, since_date="2026-09-01", group_by=GroupBy.PAYEE
    )

    assert result["until_date"] == "2026-10-04"
    assert {g["name"]: g["spent"] for g in result["groups"]} == {
        "Corner Market": 80.0,
        "Cafe": 25.0,
    }


async def test_summarize_spending_by_category_group(ynab, context):
    _seed_spending(ynab)
    ynab.add(
        "GET",
        f"{PLAN}/categories",
        {
            "category_groups": [
                {
                    "id": "grp-everyday",
                    "name": "Everyday",
                    "categories": [{"id": "cat-groceries"}, {"id": "cat-dining"}],
                },
            ],
            "server_knowledge": 1,
        },
    )

    result = await insights.summarize_spending(
        context, since_date="2026-09-01", group_by=GroupBy.CATEGORY_GROUP
    )

    assert len(ynab.requests) == 3
    assert result["groups"] == [
        {"id": "grp-everyday", "name": "Everyday", "spent": 105.0, "transaction_count": 5}
    ]


async def test_summarize_spending_rejects_reversed_dates(ynab, context):
    with pytest.raises(RetryableToolError, match="after"):
        await insights.summarize_spending(context, since_date="2026-10-01", until_date="2026-09-01")


# --- ForecastCashFlow ---


@pytest.mark.parametrize(
    ("first", "frequency", "end", "expected"),
    [
        ("2026-10-10", "never", "2026-12-31", ["2026-10-10"]),
        ("2026-10-10", "never", "2026-10-01", []),
        (
            "2026-01-31",
            "monthly",
            "2026-04-30",
            ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"],
        ),
        ("2026-10-01", "everyOtherWeek", "2026-10-31", ["2026-10-01", "2026-10-15", "2026-10-29"]),
        (
            "2026-10-01",
            "twiceAMonth",
            "2026-11-20",
            ["2026-10-01", "2026-10-16", "2026-11-01", "2026-11-16"],
        ),
        ("2026-10-05", "yearly", "2028-01-01", ["2026-10-05", "2027-10-05"]),
        ("2026-10-05", "every3Months", "2027-04-05", ["2026-10-05", "2027-01-05", "2027-04-05"]),
    ],
)
def test_occurrences(first, frequency, end, expected):
    dates = occurrences(date.fromisoformat(first), frequency, date.fromisoformat(end))
    assert [d.isoformat() for d in dates] == expected


def _seed_forecast(ynab):
    ynab.add(
        "GET",
        f"{PLAN}/accounts",
        {
            "accounts": [
                make_account(id="checking", name="Checking", balance=1000000),
                make_account(id="savings", name="Savings", type="savings", balance=5000000),
                make_account(id="closed", closed=True),
                make_account(id="brokerage", on_budget=False, balance=9000000),
            ]
        },
    )
    ynab.add(
        "GET",
        f"{PLAN}/scheduled_transactions",
        {
            "scheduled_transactions": [
                {
                    "id": "rent",
                    "date_next": "2026-10-05",
                    "frequency": "monthly",
                    "amount": -1500000,
                    "account_id": "checking",
                    "payee_name": "Landlord",
                },
                {
                    "id": "pay",
                    "date_next": "2026-10-15",
                    "frequency": "everyOtherWeek",
                    "amount": 1200000,
                    "account_id": "checking",
                    "payee_name": "Employer",
                },
                {
                    "id": "save",
                    "date_next": "2026-10-20",
                    "frequency": "monthly",
                    "amount": -100000,
                    "account_id": "checking",
                    "transfer_account_id": "savings",
                },
            ]
        },
    )


async def test_forecast_cash_flow(ynab, context):
    _seed_forecast(ynab)

    result = await insights.forecast_cash_flow(context, days=30)

    assert result["from_date"] == "2026-10-04"
    assert result["to_date"] == "2026-11-03"
    by_id = {a["account_id"]: a for a in result["accounts"]}
    assert set(by_id) == {"checking", "savings"}

    checking = by_id["checking"]
    # 1000 - 1500 (10/5) + 1200 (10/15) - 100 (10/20) + 1200 (10/29)
    assert checking["ending_balance"] == 1800.0
    assert checking["lowest_balance"] == -500.0
    assert checking["lowest_balance_date"] == "2026-10-05"
    assert checking["goes_negative"] is True
    assert [e["date"] for e in checking["upcoming"]] == [
        "2026-10-05",
        "2026-10-15",
        "2026-10-20",
        "2026-10-29",
    ]
    assert by_id["savings"]["ending_balance"] == 5100.0
    assert result["accounts"][0]["account_id"] == "checking"  # lowest first


async def test_forecast_single_account_including_tracking(ynab, context):
    _seed_forecast(ynab)

    result = await insights.forecast_cash_flow(context, account_id="brokerage")

    assert [a["account_id"] for a in result["accounts"]] == ["brokerage"]
    assert result["accounts"][0]["upcoming"] == []


async def test_forecast_unknown_account(ynab, context):
    _seed_forecast(ynab)

    with pytest.raises(RetryableToolError, match="wasn't found"):
        await insights.forecast_cash_flow(context, account_id="closed")


# --- ReviewGoals ---


async def test_review_goals(ynab, context):
    ynab.add(
        "GET",
        f"{PLAN}/months/current",
        {
            "month": {
                "month": "2026-10-01",
                "to_be_budgeted": 100000,
                "categories": [
                    make_category(
                        id="rent",
                        name="Rent",
                        goal_type="NEED",
                        goal_under_funded=0,
                        goal_target=1500000,
                    ),
                    make_category(
                        id="car",
                        name="Car",
                        goal_type="TBD",
                        goal_under_funded=150000,
                        goal_target=6000000,
                        goal_target_date="2027-06-01",
                        goal_percentage_complete=40,
                    ),
                    make_category(
                        id="gifts", name="Gifts", goal_type="NEED", goal_under_funded=25000
                    ),
                    make_category(
                        id="paused",
                        name="Paused",
                        goal_type="NEED",
                        goal_under_funded=99000,
                        goal_snoozed_at="2026-10-01",
                    ),
                    make_category(id="plain", name="Plain"),
                ],
            }
        },
    )

    result = await insights.review_goals(context)

    assert result["targets"] == 3
    assert result["on_track"] == 1
    assert result["snoozed"] == 1
    assert result["total_needed"] == 175.0
    assert result["ready_to_assign_covers_all"] is False
    assert [c["category_id"] for c in result["underfunded"]] == ["car", "gifts"]
    car = result["underfunded"][0]
    assert car["needed_this_month"] == 150.0
    assert car["target"] == 6000.0
    assert car["target_date"] == "2027-06-01"
