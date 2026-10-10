"""IDs interpolated into request paths must not change which endpoint is called."""

import pytest
from arcade_mcp_server.exceptions import RetryableToolError
from conftest import make_transaction

from arcade_ynab.tools import accounts, categories, payees, scheduled, transactions

PLAN = "/plans/last-used"


async def test_delete_scheduled_transaction_rejects_traversal_into_transactions(ynab, context):
    # Without validation this would send DELETE /plans/last-used/transactions/txn-1.
    ynab.add("DELETE", f"{PLAN}/transactions/txn-1", {"transaction": make_transaction()})

    with pytest.raises(RetryableToolError, match="Invalid scheduled_transaction_id"):
        await scheduled.delete_scheduled_transaction(
            context, scheduled_transaction_id="../transactions/txn-1"
        )
    assert ynab.requests == []


async def test_get_account_rejects_extra_segments(ynab, context):
    # Every segment is individually valid, but this would call list-transactions-by-account.
    ynab.add("GET", f"{PLAN}/accounts/acc-1/transactions", {"transactions": []})

    with pytest.raises(RetryableToolError, match="Invalid account_id"):
        await accounts.get_account(context, account_id="acc-1/transactions")
    assert ynab.requests == []


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: scheduled.delete_scheduled_transaction(ctx, scheduled_transaction_id="sch-1/x"),
        lambda ctx: transactions.list_transactions(ctx, payee_id="p-1/transactions"),
        lambda ctx: categories.get_category(ctx, category_id="c-1/x"),
        lambda ctx: categories.update_category(ctx, category_id="c-1/x", name="x"),
        lambda ctx: payees.update_payee(ctx, payee_id="p-1/x", name="x"),
        lambda ctx: accounts.list_accounts(ctx, plan_id="p-1/accounts"),
        lambda ctx: transactions.delete_transaction(ctx, transaction_id="../accounts"),
        lambda ctx: transactions.get_transaction(ctx, transaction_id="%2e%2e"),
        lambda ctx: transactions.list_transactions(ctx, account_id="../../x"),
        lambda ctx: categories.assign_to_category(ctx, category_id="..", amount=1),
        lambda ctx: categories.update_category_group(ctx, category_group_id="g#x", name="x"),
        lambda ctx: payees.update_payee(ctx, payee_id="p/../../x", name="x"),
        lambda ctx: accounts.get_account(ctx, account_id="a?x=1"),
        lambda ctx: accounts.list_accounts(ctx, plan_id="../plans"),
    ],
)
async def test_tools_reject_unsafe_ids(ynab, context, call):
    with pytest.raises(RetryableToolError, match="Invalid "):
        await call(context)
    assert ynab.requests == []
