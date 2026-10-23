"""SSH preparation never mistakes missing or ambiguous data for key rotation."""

from __future__ import annotations

import base64
import builtins
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_vault import cli, service, ssh_key
from agent_vault.extensions import ExtensionRegistry
from agent_vault.identity_key import IdentityKeyError


@pytest.fixture(scope="module")
def record():
    return ssh_key._generate_record()


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("AGENT_VAULT", "VAULT_")) or name == "KPDB":
            monkeypatch.delenv(name)
    database = tmp_path / "fixture.kdbx"
    database.write_bytes(b"disposable fixture")
    monkeypatch.setenv("KPDB", str(database))
    monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.chdir(tmp_path)
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr("agent_vault.extensions._REGISTRY", registry)
    return str(database)


def vault(monkeypatch):
    svc = service.VaultService()
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *a, **kw: True)
    monkeypatch.setattr(svc, "_check_password_ttl", lambda: None)
    return svc


def request(svc, database, **extra):
    return svc.handle_request({
        "action": "ssh-key-prepare", "kpdb": database, "entry": "SSH/node-a", "group": "",
        **extra,
    })


def test_whole_pair_is_validated_and_output_is_public_only(record):
    metadata = ssh_key._read_record(record)
    assert set(metadata) == {"public_key", "fingerprint"}
    assert metadata["public_key"].startswith("ssh-ed25519 ")
    assert metadata["fingerprint"].startswith("SHA256:")
    private = json.loads(base64.b64decode(record))["private_key"]
    assert private not in json.dumps(metadata) and record not in json.dumps(metadata)


@pytest.mark.parametrize("value", [None, "", "legacy attachment", "a" * 20000, "é", "e30="])
def test_invalid_records_fail_without_exposing_value(value):
    with pytest.raises(IdentityKeyError) as error:
        ssh_key._read_record(value)
    assert error.value.code == "malformed_key"
    assert not value or value not in str(error.value)


@pytest.mark.parametrize("mutation", ["wrong-public", "unknown-field", "bool-version", "duplicate-field"])
def test_record_mismatch_or_noncanonical_data_refused(record, mutation):
    data = json.loads(base64.b64decode(record))
    if mutation == "wrong-public":
        data["public_key"] = ssh_key._read_record(ssh_key._generate_record())["public_key"]
    elif mutation == "unknown-field":
        data["extra"] = True
    elif mutation == "bool-version":
        data["version"] = True
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    if mutation == "duplicate-field":
        encoded = '{"version":1,' + encoded[1:]
    value = base64.b64encode(encoded.encode()).decode()
    with pytest.raises(IdentityKeyError, match="invalid"):
        ssh_key._read_record(value)


def test_public_metadata_never_generates_another_private_key(monkeypatch, record):
    serialization, private_type, public_type = ssh_key._crypto()
    public = ssh_key._read_record(record)["public_key"]
    monkeypatch.setattr(
        ssh_key, "_crypto",
        lambda: (serialization, SimpleNamespace(generate=Mock(side_effect=AssertionError)), public_type),
    )
    assert ssh_key.public_metadata(public)["public_key"] == public
    assert private_type is not None


def test_missing_crypto_has_explicit_remedy(monkeypatch):
    original = builtins.__import__

    def without_crypto(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("fixture only")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_crypto)
    with pytest.raises(IdentityKeyError) as error:
        ssh_key._crypto()
    assert error.value.code == "crypto_unavailable"


@pytest.mark.parametrize("value,code", [(None, "entry_missing"), ("invalid", "malformed_key")])
def test_ordinary_preparation_never_creates_on_missing_or_invalid(
    isolated, monkeypatch, value, code,
):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: value)
    create = Mock()
    generate = Mock(side_effect=AssertionError)
    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    monkeypatch.setattr(ssh_key, "_generate_record", generate)
    result = request(svc, isolated)
    assert result["code"] == code and not result["ok"]
    create.assert_not_called()
    generate.assert_not_called()


def test_reuse_never_writes_or_generates(isolated, monkeypatch, record):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: record)
    monkeypatch.setattr(ssh_key, "_generate_record", Mock(side_effect=AssertionError))
    monkeypatch.setattr(svc.cli, "create_identity_key", Mock(side_effect=AssertionError))
    result = request(svc, isolated, initialize=True, confirm_initialization=True)
    assert result == {"ok": True, "status": "reused", **ssh_key._read_record(record)}
    assert record not in json.dumps(result)
    assert not svc.cache


@pytest.mark.parametrize("extra", [
    {"initialize": True}, {"confirm_initialization": True},
    {"initialize": "yes", "confirm_initialization": True},
    {"entry": None}, {"entry": "SSH/../x"}, {"entry": "SSH/x\n"},
    {"entry": "SSH\\x"}, {"entry": "SSH/ x"}, {"group": []},
])
def test_bad_request_fails_before_unlock_or_read(isolated, monkeypatch, extra):
    svc = vault(monkeypatch)
    unlock = Mock(side_effect=AssertionError)
    monkeypatch.setattr(svc, "ensure_unlocked", unlock)
    result = request(svc, isolated, **extra)
    assert not result["ok"] and result["code"] in ("confirmation_required", "invalid_entry")
    unlock.assert_not_called()


def test_locked_or_failed_read_never_creates(isolated, monkeypatch):
    svc = vault(monkeypatch)
    read = Mock(side_effect=IdentityKeyError("vault_read_failed", "Authoritative read failed"))
    create = Mock(side_effect=AssertionError)
    monkeypatch.setattr(svc.cli, "read_identity_key", read)
    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *a: False)
    assert request(svc, isolated, initialize=True, confirm_initialization=True)["code"] == "needs_unlock"
    read.assert_not_called()
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *a: True)
    assert request(svc, isolated, initialize=True, confirm_initialization=True)["code"] == "vault_read_failed"
    create.assert_not_called()


def test_concurrent_prepare_commits_one_record_and_reuses(isolated, monkeypatch):
    svc = vault(monkeypatch)
    values = []
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: values[0] if values else None)

    def create(database, entry, value, deadline):
        time.sleep(0.01)
        values.append(value)

    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: request(
            svc, isolated, initialize=True, confirm_initialization=True,
        ), range(4)))
    assert len(values) == 1
    assert sum(result["status"] == "created" for result in results) == 1
    assert all(result["public_key"] == results[0]["public_key"] for result in results)
    assert values[0] not in json.dumps(results)


@pytest.mark.parametrize("winner", [None, "other"])
def test_uncertain_creation_rereads_without_retry(isolated, monkeypatch, record, winner):
    svc = vault(monkeypatch)
    values = iter([None, record if winner else None])
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: next(values))
    create = Mock()
    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    result = request(svc, isolated, initialize=True, confirm_initialization=True)
    assert create.call_count == 1
    if winner:
        assert result["ok"] and result["status"] == "reused"
    else:
        assert result["code"] == "creation_unconfirmed"


def test_busy_service_refuses_without_io():
    fake_lock = Mock()
    fake_lock.acquire.return_value = False
    svc = SimpleNamespace(_credential_lock=fake_lock, _request_ctx=SimpleNamespace())
    with pytest.raises(IdentityKeyError) as error:
        ssh_key.prepare(svc, "fixture", "SSH/node-a", False, False)
    assert error.value.code == "service_busy"


def test_delayed_unlock_cannot_restart_mutation_deadline(isolated, monkeypatch):
    svc = vault(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(ssh_key.time, "monotonic", lambda: clock[0])

    def delayed_unlock(*args):
        clock[0] += 61
        return True

    monkeypatch.setattr(svc, "ensure_unlocked", delayed_unlock)
    read = Mock(side_effect=AssertionError)
    monkeypatch.setattr(svc.cli, "read_identity_key", read)
    result = request(svc, isolated, initialize=True, confirm_initialization=True)
    assert result["code"] == "operation_expired" and not result["ok"]
    read.assert_not_called()


@pytest.mark.parametrize("prompt", [False, True])
def test_actual_cli_public_output_pins_database_and_requires_running(
    isolated, monkeypatch, capsys, record, prompt,
):
    sent = []
    monkeypatch.setattr(cli, "ensure_service", Mock(side_effect=AssertionError))
    metadata = ssh_key._read_record(record)

    def send(payload, **kwargs):
        sent.append((payload, kwargs))
        if payload["action"] == "ping":
            return {"ok": True}
        return {"ok": True, "status": "reused", **metadata, "ignored_private": record}

    monkeypatch.setattr(cli, "send_command", send)
    args = ["ssh-key", "prepare", "--entry", "SSH/node-a", "--require-running", "--json"]
    assert cli.main(args + (["--prompt"] if prompt else [])) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"version": 1, "status": "reused", **metadata}
    assert record not in output.out + output.err
    payload, kwargs = sent[1]
    assert payload["kpdb"] == isolated and payload["group"] == ""
    assert payload["allow_prompt"] is prompt and not payload["initialize"]
    assert kwargs == {"timeout": 120.0 if prompt else 90.0, "strict_endpoint": True}


def test_cli_flags_fail_before_service(isolated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_service", Mock(side_effect=AssertionError))
    assert cli.main(["ssh-key", "prepare", "--entry", "SSH/node-a", "--initialize"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "confirmation_required"


@pytest.mark.parametrize("response", [None, [], "invalid", {"ok": False}])
def test_cli_stopped_required_service_never_activates(isolated, monkeypatch, capsys, response):
    monkeypatch.setattr(cli, "ensure_service", Mock(side_effect=AssertionError))
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: response)
    assert cli.main(["ssh-key", "prepare", "--entry", "SSH/node-a", "--require-running"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "service_unavailable"


@pytest.mark.parametrize("response", [
    None, {"ok": False, "code": "SECRET", "error": "SECRET"},
    {"ok": True, "status": "reused", "public_key": "SECRET"},
])
def test_cli_invalid_response_is_sanitized(isolated, monkeypatch, capsys, response):
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: response)
    assert cli.main(["ssh-key", "prepare", "--entry", "SSH/node-a"]) == 1
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err
    assert "error" in json.loads(output.out)
