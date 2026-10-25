import pytest

from agent_index import __main__ as cli


def test_native_identity_does_not_grant_plugin_cell_authority(monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_INSTALLATION_ID", raising=False)
    monkeypatch.setenv("AGENT_INDEX_NATIVE_INSTALLATION_ID", "native-instance")
    assert cli._expected_installation_id() == "native-instance"
    assert cli._validated_cell_transaction() is None
    with pytest.raises(cli.ServiceOwnershipError, match="requires a namespaced installation"):
        cli._validate_cell_start_authority(passive=False)


def test_native_identity_cannot_override_a_plugin_cell_receipt_requirement(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "marketplace/agent-index")
    monkeypatch.setenv("AGENT_INDEX_NATIVE_INSTALLATION_ID", "native-instance")
    monkeypatch.delenv("AGENT_INDEX_CELL_TRANSACTION", raising=False)
    assert cli._expected_installation_id() == "marketplace/agent-index"
    with pytest.raises(cli.ServiceOwnershipError, match="transaction receipt"):
        cli._validated_cell_transaction()
