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


def _definitions():
    return [tool.definition for tool in app._catalog]


def test_registers_expected_tools():
    names = {d.name for d in _definitions()}
    assert PHASE_1_TOOLS <= names
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
