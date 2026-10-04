from conftest import make_account, make_category, make_transaction

from arcade_ynab import shaping


def test_category_uses_ynab_app_terms():
    shaped = shaping.category(make_category())

    assert shaped["assigned"] == 500.0
    assert shaped["activity"] == -320.55
    assert shaped["available"] == 179.45
    assert shaped["available_formatted"] == "$179.45"
    assert "budgeted" not in shaped
    assert "balance" not in shaped
    assert "goal" not in shaped
    assert "note" not in shaped  # None values are dropped


def test_category_goal():
    shaped = shaping.category(
        make_category(
            goal_type="NEED",
            goal_target=600000,
            goal_target_date="2026-12-01",
            goal_percentage_complete=80,
            goal_under_funded=100000,
            goal_snoozed_at=None,
        )
    )

    assert shaped["goal"] == {
        "type": "NEED",
        "target": 600.0,
        "target_date": "2026-12-01",
        "percentage_complete": 80,
        "under_funded": 100.0,
    }


def test_account_amounts():
    shaped = shaping.account(make_account())

    assert shaped["balance"] == 1234.56
    assert shaped["balance_formatted"] == "$1,234.56"
    assert shaped["cleared_balance"] == 1200.0
    assert shaped["closed"] is False


def test_month_maps_ready_to_assign():
    shaped = shaping.month(
        {"month": "2026-09-01", "to_be_budgeted": 25000, "income": 5000000, "budgeted": 4975000}
    )

    assert shaped == {
        "month": "2026-09-01",
        "ready_to_assign": 25.0,
        "income": 5000.0,
        "assigned": 4975.0,
    }


def test_transaction_with_splits_drops_deleted_lines():
    shaped = shaping.transaction(
        make_transaction(
            category_id=None,
            category_name="Split",
            subtransactions=[
                {"id": "s1", "amount": -30000, "category_name": "Groceries", "deleted": False},
                {"id": "s2", "amount": -12500, "category_name": "Household", "deleted": False},
                {"id": "s3", "amount": -1, "deleted": True},
            ],
        )
    )

    assert shaped["amount"] == -42.5
    assert [s["id"] for s in shaped["subtransactions"]] == ["s1", "s2"]
    assert shaped["subtransactions"][1]["amount"] == -12.5


def test_live_and_truncate():
    items = shaping.live([{"id": 1}, {"id": 2, "deleted": True}, {"id": 3}])
    kept, info = shaping.truncate(items, 1)

    assert kept == [{"id": 1}]
    assert info == {"total_count": 2, "truncated": True}
