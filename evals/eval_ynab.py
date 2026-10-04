"""Tool-selection evals for the YNAB MCP server.

These check that a model picks the right tool with the right arguments for common
requests. They don't call YNAB. Run them with an LLM API key:

    ANTHROPIC_API_KEY=... uv run arcade evals evals/ -p anthropic
"""

from pathlib import Path

from arcade_evals import (
    BinaryCritic,
    EvalRubric,
    EvalSuite,
    ExpectedMCPToolCall,
    SimilarityCritic,
    tool_eval,
)

SERVER = Path(__file__).resolve().parents[1] / "src" / "arcade_ynab" / "server.py"

SYSTEM_MESSAGE = (
    "You help the user manage their YNAB budget. Today is 2026-10-04. "
    "Known IDs: checking account 'acct-checking', savings account 'acct-savings', "
    "Groceries category 'cat-groceries', Dining Out category 'cat-dining', "
    "Fun Money category 'cat-fun'. "
    "Don't ask follow-up questions; call the tool that best answers the request."
)


@tool_eval()
async def ynab_eval_suite() -> EvalSuite:
    suite = EvalSuite(
        name="YNAB tools",
        system_message=SYSTEM_MESSAGE,
        rubric=EvalRubric(fail_threshold=0.8, warn_threshold=0.9),
    )
    await suite.add_mcp_stdio_server(command=["uv", "run", str(SERVER)])

    suite.add_case(
        name="Monthly overview",
        user_message="How's my budget looking this month?",
        expected_tool_calls=[ExpectedMCPToolCall("Ynab_GetMonth", {"month": "current"})],
        critics=[BinaryCritic(critic_field="month", weight=1.0)],
    )
    suite.add_case(
        name="Spending by category last month",
        user_message="What did I spend money on in September 2026, by category?",
        expected_tool_calls=[
            ExpectedMCPToolCall(
                "Ynab_SummarizeSpending",
                {"since_date": "2026-09-01", "until_date": "2026-09-30", "group_by": "category"},
            )
        ],
        critics=[
            BinaryCritic(critic_field="since_date", weight=0.4),
            BinaryCritic(critic_field="until_date", weight=0.4),
            BinaryCritic(critic_field="group_by", weight=0.2),
        ],
    )
    suite.add_case(
        name="What needs attention",
        user_message="Which transactions do I still need to approve?",
        expected_tool_calls=[ExpectedMCPToolCall("Ynab_ReviewUnapproved", {})],
    )
    suite.add_case(
        name="Overspending",
        user_message="Did I overspend anywhere this month? What can I cover it with?",
        expected_tool_calls=[ExpectedMCPToolCall("Ynab_FindOverspending", {})],
    )
    suite.add_case(
        name="Cash flow",
        user_message="Will my checking account dip below zero in the next two weeks?",
        expected_tool_calls=[
            ExpectedMCPToolCall(
                "Ynab_ForecastCashFlow", {"days": 14, "account_id": "acct-checking"}
            )
        ],
        critics=[
            BinaryCritic(critic_field="days", weight=0.5),
            BinaryCritic(critic_field="account_id", weight=0.5),
        ],
    )
    suite.add_case(
        name="Goals",
        user_message="Which of my savings targets are behind this month?",
        expected_tool_calls=[ExpectedMCPToolCall("Ynab_ReviewGoals", {})],
    )
    suite.add_case(
        name="Record a purchase",
        user_message=(
            "I just spent $42.50 at Corner Market on groceries from checking today. Add it."
        ),
        expected_tool_calls=[
            ExpectedMCPToolCall(
                "Ynab_CreateTransaction",
                {
                    "account_id": "acct-checking",
                    "date": "2026-10-04",
                    "amount": -42.5,
                    "payee_name": "Corner Market",
                    "category_id": "cat-groceries",
                },
            )
        ],
        critics=[
            BinaryCritic(critic_field="account_id", weight=0.2),
            BinaryCritic(critic_field="date", weight=0.2),
            BinaryCritic(critic_field="amount", weight=0.3),
            SimilarityCritic(critic_field="payee_name", weight=0.1),
            BinaryCritic(critic_field="category_id", weight=0.2),
        ],
    )
    suite.add_case(
        name="Move money",
        user_message="Move $25 from Fun Money to Dining Out.",
        expected_tool_calls=[
            ExpectedMCPToolCall(
                "Ynab_MoveMoney",
                {"amount": 25, "from_category_id": "cat-fun", "to_category_id": "cat-dining"},
            )
        ],
        critics=[
            BinaryCritic(critic_field="amount", weight=0.4),
            BinaryCritic(critic_field="from_category_id", weight=0.3),
            BinaryCritic(critic_field="to_category_id", weight=0.3),
        ],
    )
    suite.add_case(
        name="Transfer between accounts",
        user_message="Transfer $200 from checking to savings today.",
        expected_tool_calls=[
            ExpectedMCPToolCall(
                "Ynab_CreateTransaction",
                {
                    "account_id": "acct-checking",
                    "date": "2026-10-04",
                    "amount": -200,
                    "transfer_account_id": "acct-savings",
                },
            )
        ],
        critics=[
            BinaryCritic(critic_field="account_id", weight=0.25),
            BinaryCritic(critic_field="amount", weight=0.25),
            BinaryCritic(critic_field="transfer_account_id", weight=0.5),
        ],
    )
    suite.add_case(
        name="Grocery transactions",
        user_message="Show me my grocery transactions since September 1st 2026.",
        expected_tool_calls=[
            ExpectedMCPToolCall(
                "Ynab_ListTransactions",
                {"category_id": "cat-groceries", "since_date": "2026-09-01"},
            )
        ],
        critics=[
            BinaryCritic(critic_field="category_id", weight=0.5),
            BinaryCritic(critic_field="since_date", weight=0.5),
        ],
    )
    return suite
