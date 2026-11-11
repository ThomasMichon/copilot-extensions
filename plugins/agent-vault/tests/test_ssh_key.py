"""SSH preparation never mistakes missing or ambiguous data for key rotation."""

from __future__ import annotations

import base64
import builtins
import json
import os
import shutil
import subprocess
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
    backend = ssh_key._crypto()
    public = ssh_key._read_record(record)["public_key"]
    monkeypatch.setattr(
        ssh_key, "_crypto",
        lambda: SimpleNamespace(
            import_key=backend.import_key, generate=Mock(side_effect=AssertionError),
        ),
    )
    assert ssh_key.public_metadata(public)["public_key"] == public


def test_missing_crypto_has_explicit_remedy(monkeypatch):
    original = builtins.__import__

    def without_crypto(name, *args, **kwargs):
        if name.startswith("Cryptodome"):
            raise ImportError("fixture only")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_crypto)
    with pytest.raises(IdentityKeyError) as error:
        ssh_key._crypto()
    assert error.value.code == "crypto_unavailable"


def test_record_roundtrip_without_cryptography(monkeypatch):
    original = builtins.__import__

    def without_cryptography(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("fixture only")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_cryptography)
    value = ssh_key._generate_record()
    assert ssh_key.decode_record(value)["public_key"] == ssh_key._read_record(value)["public_key"]


@pytest.mark.parametrize("damage", ["outer-public", "pair-public", "seed", "check", "padding", "kdf"])
def test_damaged_openssh_fields_are_not_silently_adopted(record, damage):
    data = ssh_key.decode_record(record)
    blob = bytearray(base64.b64decode("".join(data["private_key"].splitlines()[1:-1])))
    public_wire = base64.b64decode(data["public_key"].split(" ")[1])
    outer = blob.index(public_wire)
    raw_public = public_wire[-32:]
    private_public = blob.rindex(raw_public)
    if damage == "outer-public":
        blob[outer + len(public_wire) - 1] ^= 1
    elif damage == "pair-public":
        blob[private_public] ^= 1
    elif damage == "seed":
        blob[private_public - 32] ^= 1
    elif damage == "check":
        blob[outer + len(public_wire) + 4] ^= 1
    elif damage == "kdf":
        blob[blob.index(b"none") + 8] ^= 1
    else:
        blob[-1] ^= 1
    encoded = base64.b64encode(blob).decode("ascii")
    data["private_key"] = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n" + encoded
        + "\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    damaged = base64.b64encode(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("ascii"),
    ).decode("ascii")
    with pytest.raises(IdentityKeyError) as error:
        ssh_key.decode_record(damaged)
    assert error.value.code == "malformed_key"


def test_existing_cryptography_records_and_new_records_interoperate():
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    private = ed25519.Ed25519PrivateKey.generate()
    data = {
        "schema": "agent-vault.ssh-key", "version": 1,
        "private_key": private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode("ascii"),
        "public_key": private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode("ascii"),
    }
    value = base64.b64encode(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("ascii"),
    ).decode("ascii")
    assert ssh_key.decode_record(value) == data
    new = ssh_key.decode_record(ssh_key._generate_record())
    parsed = serialization.load_ssh_private_key(new["private_key"].encode("ascii"), None)
    assert parsed.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ).decode("ascii") == new["public_key"]


def test_openssh_reads_new_key_and_existing_key_is_reused(tmp_path):
    executable = shutil.which("ssh-keygen")
    if not executable:
        pytest.skip("OpenSSH is unavailable")
    directory = tmp_path / "keys"
    if os.name == "nt":
        from agent_vault.ssh_key_files import WindowsSecurity

        security = WindowsSecurity()
        security.create(directory, directory=True)
    else:
        directory.mkdir(mode=0o700)
    path = directory / "id_ed25519"
    data = ssh_key.decode_record(ssh_key._generate_record())
    descriptor = (
        security.create(path, directory=False) if os.name == "nt"
        else os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data["private_key"].encode("ascii"))
    options = {
        "capture_output": True, "text": True, "timeout": 10,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    result = subprocess.run([executable, "-y", "-f", str(path)], **options)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == data["public_key"]
    path.unlink()
    result = subprocess.run(
        [executable, "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)], **options,
    )
    assert result.returncode == 0, result.stderr
    existing = {
        "schema": "agent-vault.ssh-key", "version": 1,
        "private_key": path.read_text(encoding="ascii"),
        "public_key": path.with_suffix(".pub").read_text(encoding="ascii").strip(),
    }
    value = base64.b64encode(
        json.dumps(existing, sort_keys=True, separators=(",", ":")).encode("ascii"),
    ).decode("ascii")
    assert ssh_key.decode_record(value) == existing


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
