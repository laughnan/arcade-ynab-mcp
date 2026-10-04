from arcade_mcp_server.metadata import ServiceDomain

from arcade_ynab.server import app

PHASE_1_TOOLS = {
    "GetUser",
    "ListPlans",
    "GetPlanSettings",
    "GetMonth",
    "ListMonths",
    "ListAccounts",
    "GetAccount",
    "ListCategories",
    "GetCategory",
    "ListPayees",
    "ListTransactions",
    "GetTransaction",
    "ListScheduledTransactions",
    "ListMoneyMovements",
}

PHASE_2_TOOLS = {
    "CreateTransaction",
    "UpdateTransactions",
    "DeleteTransaction",
    "ImportTransactions",
    "AssignToCategory",
    "MoveMoney",
    "CreateCategory",
    "UpdateCategory",
    "CreateCategoryGroup",
    "UpdateCategoryGroup",
    "CreatePayee",
    "UpdatePayee",
    "CreateAccount",
    "CreateScheduledTransaction",
    "UpdateScheduledTransaction",
    "DeleteScheduledTransaction",
}


def _definitions():
    return [tool.definition for tool in app._catalog]


def test_registers_expected_tools():
    names = {d.name for d in _definitions()}
    assert PHASE_1_TOOLS | PHASE_2_TOOLS <= names
    assert all(str(d.fully_qualified_name).startswith("Ynab.") for d in _definitions())


def test_every_tool_uses_ynab_oauth_and_no_secrets():
    for d in _definitions():
        auth = d.requirements.authorization
        assert auth is not None, d.name
        assert auth.id == "ynab", d.name
        assert not d.requirements.secrets, d.name


def test_every_tool_declares_metadata():
    for d in _definitions():
        assert d.metadata is not None, d.name
        assert d.metadata.classification.service_domains == [ServiceDomain.FINANCIAL_DATA]
        assert d.metadata.behavior.open_world is True


def test_phase_1_tools_are_read_only():
    for d in _definitions():
        if d.name in PHASE_1_TOOLS:
            assert d.metadata.behavior.read_only is True, d.name
            assert d.metadata.behavior.destructive is False, d.name


def test_every_tool_has_a_description():
    for d in _definitions():
        assert d.description and len(d.description) > 20, d.name


def test_phase_2_tools_are_writes():
    for d in _definitions():
        if d.name in PHASE_2_TOOLS:
            behavior = d.metadata.behavior
            assert behavior.read_only is False, d.name
            assert behavior.destructive is d.name.startswith("Delete"), d.name


def test_move_money_is_not_idempotent():
    move = next(d for d in _definitions() if d.name == "MoveMoney")
    assert move.metadata.behavior.idempotent is False
