from credential_relay.account_relay import AccountRelay
from credential_relay.registry import RelayBuilder

from agent_bridge.relay_runtime import capture_relay, restore_relay
from agent_bridge.agent_registry_relay import register_credential_sources


def test_optional_policy_is_attached_to_bridge_not_a_provider_daemon(tmp_path, monkeypatch):
    from agent_bridge import config, agent_registry_relay, http_provider_sources

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_registry_relay, "_register_provider_relay", lambda *a: None)
    monkeypatch.setattr(http_provider_sources, "register_http_credential_sources", lambda *a: None)
    builder = RelayBuilder()
    register_credential_sources(builder)
    assert builder.empty and builder.account_relay is None
    (tmp_path / "account-relay.json").write_text("{}")
    register_credential_sources(builder)
    assert not builder.empty
    assert type(builder.account_relay) is AccountRelay
    assert builder.build().account_relay is builder.account_relay


def test_account_policy_reference_survives_private_cutover_without_reading_tokens(tmp_path):
    builder = RelayBuilder()
    builder.account_relay = AccountRelay(str(tmp_path / "not-yet-readable.json"))
    server = builder.build()
    payload = capture_relay(server)
    assert payload["account_policy"] == str(tmp_path / "not-yet-readable.json")
    restored = restore_relay(payload)
    assert restored.account_relay.policy_path == server.account_relay.policy_path
    assert restored.account_relay._server is None


def test_legacy_capture_has_no_new_required_field():
    server = RelayBuilder().build()
    payload = capture_relay(server)
    assert "account_policy" not in payload
    assert capture_relay(restore_relay(payload)) == payload
