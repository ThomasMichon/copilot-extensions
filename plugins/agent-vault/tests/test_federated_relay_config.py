import json
from pathlib import Path
from typing import Any

import pytest

from agent_vault.federated_relay_config import load


def selection(root: Path) -> dict[str, Any]:
    return {
        "schema": "agent-vault.federated-relay", "version": 1, "mode": "federated-relay",
        "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
        "installation": "vault-test", "service": "vault-available-v1", "prefix": "/api",
        "gateway_origin": "https://gateway.invalid", "conduit_file": str(root / "conduit.json"),
        "gateway_ca": None, "proof_key_file": str(root / "proof.pem"),
        "account_policy_file": str(root / "policy.json"),
        "grants": [{
            "principal": "caller-a", "source": "local", "account": "account-a",
            "recipient": "consumer-a", "host": "github.com", "action": "get-github-token",
        }],
        "auth": {"kind": "machine", "principal": "service-a", "audience": "gateway"},
    }


def test_absent_and_explicit_disabled_require_no_optional_runtime(tmp_path: Path) -> None:
    assert load(tmp_path) is None
    (tmp_path / "federated-relay.json").write_text(json.dumps({
        "schema": "agent-vault.federated-relay", "version": 1, "mode": "disabled",
    }))
    assert load(tmp_path) is None


@pytest.mark.parametrize("change", [
    {"mode": "relay"}, {"version": True}, {"extra": "ignored"},
    {"proof_key_file": "relative.pem"}, {"gateway_origin": "http://gateway.invalid"},
    {"gateway_origin": "https://gateway.invalid/path"},
    {"auth": {"kind": "command", "argv": ["login"]}},
    {"auth": {"kind": "az-login", "resource": "*", "tenant": "tenant"}},
    {"grants": []},
])
def test_explicit_invalid_selection_never_silently_disables(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    (tmp_path / "federated-relay.json").write_text(json.dumps(selection(tmp_path) | change))
    with pytest.raises(ValueError):
        load(tmp_path)


def test_native_selection_retains_exact_references_and_rejects_duplicate_fields(tmp_path: Path) -> None:
    path = tmp_path / "federated-relay.json"
    expected = selection(tmp_path)
    path.write_text(json.dumps(expected))
    assert load(tmp_path) == expected
    path.write_text('{"schema":"agent-vault.federated-relay","version":1,"mode":"disabled",'
                    '"mode":"federated-relay"}')
    with pytest.raises(ValueError, match="duplicate"):
        load(tmp_path)


@pytest.mark.parametrize("length", [128, 129])
def test_federated_slot_matches_connector_length_bound(tmp_path: Path, length: int) -> None:
    value = selection(tmp_path) | {"slot": "s" * length}
    (tmp_path / "federated-relay.json").write_text(json.dumps(value))
    if length == 128:
        assert load(tmp_path) == value
    else:
        with pytest.raises(ValueError, match="relay-slot-invalid"):
            load(tmp_path)
