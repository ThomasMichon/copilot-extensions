"""Owner-key creation must never confuse failure with authoritative absence."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_vault import cli, config, identity_key, keepassxc, service
from agent_vault.cache import PersistentCache
from agent_vault.extensions import ExtensionRegistry

KEY = base64.b64encode(bytes(range(32))).decode()
usable_cache = pytest.mark.skipif(
    not PersistentCache()._crypto_available(),
    reason="protected-cache decoder unavailable (POSIX optional cryptography)",
)


def identity_cache():
    return PersistentCache(identity_key.source_context()["env"]["AGENT_VAULT_CACHE_DIR"])


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    db = tmp_path / "fixture.kdbx"
    db.write_bytes(b"disposable fixture")
    for name in tuple(os.environ):
        if name.startswith("AGENT_VAULT") or name in ("KPDB", "VAULT_GROUP"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("KPDB", str(db))
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr("agent_vault.extensions._REGISTRY", registry)
    return str(db)


def completed(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "SECRET diagnostic")


@pytest.mark.parametrize("result", [None, completed(code=1), completed("Identity/x\nIdentity/x\n"), completed(" bad\n")])
def test_authoritative_listing_failure_never_absence(monkeypatch, result):
    backend = keepassxc.KeePassXCBackend()
    monkeypatch.setattr(backend, "_run", lambda *a, **kw: result)
    with pytest.raises(identity_key.IdentityKeyError):
        backend.read_identity_key("fixture", "Identity/x", time.monotonic() + 10)


def test_strict_absence_and_read_failure(monkeypatch):
    backend = keepassxc.KeePassXCBackend()
    monkeypatch.setattr(backend, "_run", lambda *a, **kw: completed(""))
    assert backend.read_identity_key("fixture", "Identity/x", time.monotonic() + 10) is None
    results = iter([completed("Identity/\nIdentity/x\n"), completed(code=1)])
    monkeypatch.setattr(backend, "_run", lambda *a, **kw: next(results))
    with pytest.raises(identity_key.IdentityKeyError, match="read failed"):
        backend.read_identity_key("fixture", "Identity/x", time.monotonic() + 10)


def test_create_uses_stdin_and_sanitizes_ambiguous_completion(monkeypatch):
    monkeypatch.setattr(
        "agent_vault.identity_key_lock.database_creation_lock",
        lambda *args: contextlib.nullcontext(),
    )
    backend = keepassxc.KeePassXCBackend()
    backend._cli_path = "fixture-cli"
    backend.set_password("fixture", "MASTER")
    monkeypatch.setattr(backend, "_identity_listing", lambda *a: {"Identity/"})
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, 1, output=KEY, stderr="MASTER")

    monkeypatch.setattr(keepassxc.subprocess, "run", run)
    backend.create_identity_key("fixture", "Identity/x", KEY, time.monotonic() + 10)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert KEY not in str(argv) and "MASTER" not in str(argv)
    assert kwargs["input"] == f"MASTER\n{KEY}\n"
    assert argv[1] == "add" and "edit" not in argv


def vault(monkeypatch):
    svc = service.VaultService()
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *a, **kw: True)
    monkeypatch.setattr(svc, "_check_password_ttl", lambda: None)
    return svc


def request(svc, db, **extra):
    return svc.handle_request({"action": "identity-key-setup", "kpdb": db, "entry": "Identity/x", "group": "", **extra})


@pytest.mark.parametrize("value,code", [(None, "entry_missing"), ("invalid", "malformed_key")])
def test_normal_setup_refuses_absent_or_malformed(isolated, monkeypatch, value, code):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: value)
    create = Mock()
    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    result = request(svc, isolated)
    assert result["code"] == code and not result["ok"]
    create.assert_not_called()
    assert value != "invalid" or "invalid" not in json.dumps(result)


def test_concurrent_initialize_and_retry_reuse(isolated, monkeypatch):
    svc = vault(monkeypatch)
    values = []
    calls = []
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: values[0] if values else None)

    def create(db, entry, value, deadline):
        calls.append(value)
        time.sleep(0.01)
        values.append(value)

    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: request(svc, isolated, initialize_owner=True, confirm_first_owner=True), range(4)))
    assert len(calls) == 1
    assert sum(r["status"] == "created" for r in results) == 1
    assert all(r["value"] == values[0] for r in results)


def test_ambiguous_add_reconciles_winner(isolated, monkeypatch):
    svc = vault(monkeypatch)
    values = iter([None, KEY])
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *a: next(values))
    monkeypatch.setattr(svc.cli, "create_identity_key", lambda *a: None)
    result = request(svc, isolated, initialize_owner=True, confirm_first_owner=True)
    assert result["ok"] and result["value"] == KEY and result["status"] == "reused"


def test_locked_never_reads_or_creates(isolated, monkeypatch):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc, "ensure_unlocked", lambda *a: False)
    read = Mock()
    monkeypatch.setattr(svc.cli, "read_identity_key", read)
    assert request(svc, isolated)["code"] == "needs_unlock"
    read.assert_not_called()


def test_context_readonly_and_explicit_empty_pinning(isolated, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("context started service"))
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: pytest.fail("context contacted service"))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert cli.main(["identity-key", "context", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["version"] == 1 and context["env"]["KPDB"] == isolated
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    for key, value in context["env"].items():
        monkeypatch.setenv(key, value)
    (tmp_path / ".agent-vault.json").write_text('{"vault":"Other","group":"Other","kpdb":"other.kdbx"}')
    resolved = config.resolve_context(strict=True)
    assert resolved.kpdb == isolated and resolved.group is None and resolved.vault_name is None
    assert PersistentCache()._base_dir == Path(context["env"]["AGENT_VAULT_CACHE_DIR"])
    assert PersistentCache()._base_dir.parent.parent == tmp_path / "cache"


@pytest.mark.parametrize("prompt", [False, True])
@usable_cache
def test_cli_only_selected_cache_and_secret_free(isolated, monkeypatch, capsys, prompt):
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    requests = []

    def send(payload, **kwargs):
        requests.append((payload, kwargs))
        return {"ok": True, "value": KEY, "generation": 10, "status": "reused"}

    monkeypatch.setattr(cli, "send_command", send)
    args = ["identity-key", "setup", "--entry", "Identity/x"]
    assert cli.main(args + (["--prompt"] if prompt else [])) == 0
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
    assert json.loads(output.out) == {"version": 1, "status": "reused", "cached": True}
    assert requests[0][0]["allow_prompt"] == prompt
    assert requests[0][1]["timeout"] == (120 if prompt else 90)
    assert requests[0][1]["strict_endpoint"]
    assert KEY not in str(requests)
    cache = identity_cache()
    assert cache.get("Identity/x") == KEY
    assert list(cache._read_store()["entries"]) == ["Identity/x"]


def test_cli_requires_both_flags_before_service(isolated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("service started"))
    assert cli.main(["identity-key", "setup", "--initialize-owner"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "confirmation_required"


@usable_cache
def test_cache_failure_never_success_or_secret(isolated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: {"ok": True, "value": KEY, "status": "reused"})
    monkeypatch.setattr(PersistentCache, "reconcile_authoritative", lambda *a: False)
    assert cli.main(["identity-key", "setup"]) == 1
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
    assert json.loads(output.out)["error"] == "cache_update_failed"


def test_backend_real_subprocess_stdin_contract(monkeypatch):
    monkeypatch.setattr(
        "agent_vault.identity_key_lock.database_creation_lock",
        lambda *args: contextlib.nullcontext(),
    )
    backend = keepassxc.KeePassXCBackend()
    backend._cli_path = "fixture-cli"
    backend.set_password("fixture", "fixture-master")
    native_run = subprocess.run
    program = (
        "import sys,base64;"
        "data=sys.stdin.read();"
        "action=sys.argv[1];"
        "print('Identity/\\nIdentity/x' if action=='ls' else "
        "base64.b64encode(bytes(range(32))).decode() if action=='show' else '',end='\\n')"
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["input"]))
        return native_run([sys.executable, "-c", program, *argv[1:]], **kwargs)

    monkeypatch.setattr(keepassxc.subprocess, "run", run)
    assert backend.read_identity_key("fixture", "Identity/x", time.monotonic() + 10) == KEY
    monkeypatch.setattr(backend, "_identity_listing", lambda *a: {"Identity/"})
    backend.create_identity_key("fixture", "Identity/x", KEY, time.monotonic() + 10)
    assert all(KEY not in str(argv) and "fixture-master" not in str(argv) for argv, _ in calls)
    assert calls[-1][1] == "fixture-master\n" + KEY + "\n"


def test_context_fresh_process_uses_pinned_empty_selection(isolated, tmp_path):
    context = identity_key.source_context()
    (tmp_path / ".agent-vault.json").write_text('{"group":"Different","vault":"Different"}')
    environment = dict(os.environ, **context["env"])
    result = subprocess.run(
        [sys.executable, "-m", "agent_vault", "identity-key", "context", "--json"],
        env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    fresh = json.loads(result.stdout)
    assert fresh["env"]["VAULT_GROUP"] == fresh["env"]["AGENT_VAULT"] == ""
    assert fresh["env"]["AGENT_VAULT_CACHE_DIR"] == context["env"]["AGENT_VAULT_CACHE_DIR"]


@pytest.mark.parametrize("consent", [(True, False), (False, True)])
def test_service_requires_both_initialization_flags(isolated, monkeypatch, consent):
    svc = vault(monkeypatch)
    read = Mock()
    monkeypatch.setattr(svc.cli, "read_identity_key", read)
    result = request(svc, isolated, initialize_owner=consent[0], confirm_first_owner=consent[1])
    assert result["code"] == "confirmation_required"
    read.assert_not_called()


def test_read_failure_cannot_initialize(isolated, monkeypatch):
    svc = vault(monkeypatch)
    def fail(*args):
        raise identity_key.IdentityKeyError("vault_read_failed", "Authoritative read failed.")
    monkeypatch.setattr(svc.cli, "read_identity_key", fail)
    create = Mock()
    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    assert request(svc, isolated, initialize_owner=True, confirm_first_owner=True)["code"] == "vault_read_failed"
    create.assert_not_called()


def test_malformed_existing_key_cannot_initialize(isolated, monkeypatch):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *args: "bad-key")
    create = Mock()
    monkeypatch.setattr(svc.cli, "create_identity_key", create)
    assert request(svc, isolated, initialize_owner=True, confirm_first_owner=True)["code"] == "malformed_key"
    create.assert_not_called()


def test_unconfirmed_creation_fails_closed(isolated, monkeypatch):
    svc = vault(monkeypatch)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *args: None)
    monkeypatch.setattr(svc.cli, "create_identity_key", lambda *args: None)
    assert request(svc, isolated, initialize_owner=True, confirm_first_owner=True)["code"] == "creation_unconfirmed"
    assert not svc.cache


def test_cache_disabled_refuses_before_service(isolated, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_VAULT_CACHE_DIR")
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("started service without cache"))
    assert cli.main(["identity-key", "setup"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "cache_unavailable"


@pytest.mark.parametrize("prompt", [False, True])
def test_service_prompt_is_explicit(isolated, monkeypatch, prompt):
    svc = service.VaultService()
    svc.cli._cli_path = "fixture-cli"
    svc._check_password_ttl = lambda: None
    monkeypatch.setattr(svc.cli, "verify_password", lambda *args: True)
    monkeypatch.setattr(svc.cli, "read_identity_key", lambda *args: KEY)
    prompts = []
    monkeypatch.setattr(service, "prompt_password", lambda *args: prompts.append(True) or "fixture-master")
    result = request(svc, isolated, allow_prompt=prompt)
    assert result["ok"] == prompt
    assert bool(prompts) == prompt


@usable_cache
def test_cli_reconciles_pending_and_preserves_unrelated_entries(isolated, monkeypatch, capsys):
    cache = identity_cache()
    assert cache.put("Unrelated/credential", "password", "fixture-other")
    assert cache.begin_replace("Identity/x", "password", "fixture-pending", 1)
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: {"ok": True, "value": KEY, "generation": 10, "status": "reused"})
    assert cli.main(["identity-key", "setup", "--entry", "Identity/x"]) == 0
    assert cache.get("Identity/x") == KEY
    assert cache.get("Unrelated/credential") == "fixture-other"
    assert cache.pending_replace("Identity/x", "password") is None
    assert KEY not in capsys.readouterr().out


def test_expired_backend_operation_never_starts_subprocess(monkeypatch):
    backend = keepassxc.KeePassXCBackend()
    run = Mock()
    monkeypatch.setattr(backend, "_run", run)
    with pytest.raises(identity_key.IdentityKeyError, match="expired"):
        backend.read_identity_key("fixture", "Identity/x", time.monotonic() - 1)
    run.assert_not_called()


@usable_cache
def test_cli_never_echoes_backend_diagnostics(isolated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **kw: {"ok": False, "error": KEY, "code": KEY})
    assert cli.main(["identity-key", "setup"]) == 1
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
    assert json.loads(output.out)["error"] == "service_unavailable"


@pytest.mark.parametrize("group", ["", "Pinned"])
@usable_cache
def test_fresh_cache_reader_honors_json_pin_over_other_selection(
    isolated, monkeypatch, tmp_path, group,
):
    monkeypatch.setenv("VAULT_GROUP", group)
    context = identity_key.source_context()
    pin = json.loads(context["env"][config.PINNED_CONTEXT_ENV])
    assert pin == {
        "version": 1, "kpdb": isolated, "group": group, "vault_name": "", "port": 19999,
    }
    entry = f"{group}/owner" if group else "owner"
    cache = identity_cache()
    assert cache.put(entry, "password", KEY)
    assert cache.put("Different/owner", "password", "fixture-wrong-key")
    (tmp_path / ".agent-vault.json").write_text('{"group":"Different","vault":"Different"}')
    environment = dict(os.environ, **context["env"])
    environment.update(KPDB="wrong.kdbx", VAULT_GROUP="Different", AGENT_VAULT="Different")
    result = subprocess.run(
        [sys.executable, "-m", "agent_vault", "get", "owner", "password", "--cache-only"],
        env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == KEY
    assert result.stderr == ""


@pytest.mark.parametrize("value", [
    "", "1", "{}", "[]", '{"version":1}',
    '{"version":true,"kpdb":"relative","group":"","vault_name":"","port":19999}',
])
def test_invalid_json_pin_fails_without_config_or_cache_fallback(
    isolated, monkeypatch, capsys, value,
):
    monkeypatch.setenv(config.PINNED_CONTEXT_ENV, value)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("invalid pin started service"))
    with pytest.raises(RuntimeError, match="Pinned vault context"):
        config.resolve_context()
    assert cli.main(["get", "owner", "password", "--cache-only"]) == 1
    assert capsys.readouterr().out == ""
    assert cli.main(["identity-key", "context", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "context_invalid"


def test_context_captures_cell_not_inherited_aw_plugin_root(isolated, monkeypatch):
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "fixture-unrelated-aw-payload")
    monkeypatch.setenv(config.INSTALLATION_ID_ENV, "fixture-cell")
    monkeypatch.setenv(config.ENDPOINT_ENV, "tcp:127.0.0.1:30001")
    monkeypatch.setenv(config.RUN_DIR_ENV, "fixture-run")
    context = identity_key.source_context()
    assert context["env"][config.INSTALLATION_ID_ENV] == "fixture-cell"
    assert context["env"][config.ENDPOINT_ENV] == "tcp:127.0.0.1:30001"
    assert context["env"][config.RUN_DIR_ENV] == str(Path("fixture-run").resolve())
    assert "COPILOT_PLUGIN_ROOT" not in context["env"]
    assert all(isinstance(value, str) for value in context["env"].values())


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("kpdb", "relative.kdbx"),
    ("group", None), ("group", "/Other/"), ("vault_name", None),
    ("port", True), ("port", 65536), ("extra", "forbidden"),
])
def test_pin_validates_version_absolute_path_fields_and_types(
    isolated, monkeypatch, field, value,
):
    pin = json.loads(identity_key.source_context()["env"][config.PINNED_CONTEXT_ENV])
    pin[field] = value
    monkeypatch.setenv(config.PINNED_CONTEXT_ENV, json.dumps(pin))
    with pytest.raises(RuntimeError):
        config.resolve_context()


def test_pin_refuses_duplicate_json_fields(isolated, monkeypatch):
    pin = identity_key.source_context()["env"][config.PINNED_CONTEXT_ENV]
    monkeypatch.setenv(config.PINNED_CONTEXT_ENV, pin[:-1] + ',"version":1}')
    with pytest.raises(RuntimeError, match="valid JSON"):
        config.resolve_context()


def test_named_vault_and_json_pin_work_without_legacy_selection_env(
    isolated, monkeypatch,
):
    Path(os.environ[config.CONFIG_ENV]).write_text(json.dumps({
        "vaults": {"Work": {"kpdb": isolated, "group": "Pinned"}},
        "default_vault": "Work",
    }))
    context = identity_key.source_context()
    for name, value in context["env"].items():
        monkeypatch.setenv(name, value)
    for name in ("KPDB", "VAULT_GROUP", "AGENT_VAULT", "AGENT_VAULT_PORT"):
        monkeypatch.delenv(name)
    Path(".agent-vault.json").write_text("not JSON")
    resolved = config.resolve_context(strict=True)
    assert resolved.kpdb == isolated
    assert resolved.group == "Pinned"
    assert resolved.vault_name == "Work"


@usable_cache
def test_pinned_cache_reader_never_migrates_an_unselected_legacy_entry(
    isolated, monkeypatch, capsys,
):
    monkeypatch.setenv("VAULT_GROUP", "Pinned")
    context = identity_key.source_context()
    cache = identity_cache()
    assert cache.put("owner", "password", KEY)
    for name, value in context["env"].items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only reader started service"))
    assert cli.main(["get", "owner", "password", "--cache-only"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert KEY not in output.err
    assert cache.get("owner") == KEY
    assert cache.get("Pinned/owner") is None


@usable_cache
def test_pinned_missing_cache_reader_and_context_create_no_files(
    isolated, monkeypatch, tmp_path, capsys,
):
    root = tmp_path / "uncreated-cache"
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(root))
    context = identity_key.source_context()
    for name, value in context["env"].items():
        monkeypatch.setenv(name, value)
    assert cli.main(["identity-key", "context", "--json"]) == 0
    assert not root.exists()
    capsys.readouterr()
    assert cli.main(["get", "Identity/x", "password", "--cache-only"]) == 1
    assert "cache_missing" in capsys.readouterr().err
    assert not root.exists()


@usable_cache
def test_setup_corrupt_cache_has_distinct_failure_before_service(
    isolated, monkeypatch, tmp_path, capsys,
):
    root = Path(identity_key.source_context()["env"]["AGENT_VAULT_CACHE_DIR"])
    root.mkdir(parents=True)
    (root / "credential-cache.enc").write_bytes(b"unreadable fixture")
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("corrupt cache started service"))
    assert cli.main(["identity-key", "setup", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "cache_read_failed"
    assert not (root / "credential-cache.key").exists()


def test_context_env_exact_aw_allowlist(isolated):
    expected = {
        "KPDB", "VAULT_GROUP", "AGENT_VAULT", "AGENT_VAULT_PINNED_CONTEXT",
        "AGENT_VAULT_CACHE", "AGENT_VAULT_CACHE_DIR", "AGENT_VAULT_HOME",
        "AGENT_VAULT_INSTALLATION_ID", "AGENT_VAULT_CONFIG", "AGENT_VAULT_RUN_DIR",
        "AGENT_VAULT_ENDPOINT", "AGENT_VAULT_HOST", "AGENT_VAULT_PORT",
        "AGENT_VAULT_PIPE", "AGENT_VAULT_SOCKET",
        "AGENT_VAULT_PID", "AGENT_VAULT_LOG", "AGENT_VAULT_SYSTEMD_UNIT",
        "AGENT_VAULT_TASK_NAME", "AGENT_VAULT_WINDOWS_RUN_DIR",
        "COPILOT_EXTENSIONS_CONTEXT",
    }
    assert set(identity_key.source_context()["env"]) == expected


@usable_cache
def test_pinned_corrupt_cache_read_is_sanitized_and_never_rekeyed(
    isolated, monkeypatch, capsys,
):
    context = identity_key.source_context()
    root = Path(context["env"]["AGENT_VAULT_CACHE_DIR"])
    root.mkdir(parents=True)
    (root / "credential-cache.enc").write_bytes(b"fixture-corrupt-cache")
    for name, value in context["env"].items():
        monkeypatch.setenv(name, value)
    assert cli.main(["get", "Identity/x", "password", "--cache-only"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "cache_read_failed" in output.err
    assert "fixture-corrupt-cache" not in output.err
    assert not (root / "credential-cache.key").exists()


@usable_cache
def test_same_entry_in_two_databases_has_isolated_identity_caches(
    isolated, monkeypatch, tmp_path, capsys,
):
    second = tmp_path / "second.kdbx"
    second.write_bytes(b"second disposable fixture")
    key_b = base64.b64encode(bytes(reversed(range(32)))).decode()
    legacy = PersistentCache()
    assert legacy.put("Identity/x", "password", "fixture-legacy-value")
    legacy_bytes = legacy._cache_file.read_bytes()
    contexts = []
    monkeypatch.setattr(cli, "ensure_service", lambda: True)

    def send(payload, **kwargs):
        value = KEY if payload["kpdb"] == isolated else key_b
        return {"ok": True, "value": value, "generation": 10, "status": "reused"}

    monkeypatch.setattr(cli, "send_command", send)
    for database in (isolated, str(second)):
        monkeypatch.setenv("KPDB", database)
        contexts.append(identity_key.source_context())
        assert cli.main(["identity-key", "setup", "--entry", "Identity/x", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "reused"
    assert contexts[0]["env"]["AGENT_VAULT_CACHE_DIR"] != contexts[1]["env"]["AGENT_VAULT_CACHE_DIR"]
    assert legacy._cache_file.read_bytes() == legacy_bytes
    assert legacy.get("Identity/x") == "fixture-legacy-value"
    for context, expected in zip(contexts, (KEY, key_b), strict=True):
        result = subprocess.run(
            [sys.executable, "-m", "agent_vault", "get", "Identity/x", "password", "--cache-only"],
            env=dict(os.environ, **context["env"]), cwd=tmp_path,
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0 and result.stdout.strip() == expected


@pytest.mark.parametrize("variable", [config.HOME_ENV, config.INSTALLATION_ID_ENV])
def test_identity_cache_is_stable_and_installation_scoped(
    isolated, monkeypatch, tmp_path, variable,
):
    first = identity_key.source_context()
    assert identity_key.source_context() == first
    monkeypatch.setenv(variable, str(tmp_path / "different-cell") if variable == config.HOME_ENV else "different-cell")
    other = identity_key.source_context()
    assert other["env"]["AGENT_VAULT_CACHE_DIR"] != first["env"]["AGENT_VAULT_CACHE_DIR"]
    for name, value in other["env"].items():
        monkeypatch.setenv(name, value)
    assert identity_key.source_context() == other


@pytest.mark.parametrize("variable,value", [
    ("AGENT_VAULT_CACHE", ""), ("AGENT_VAULT_CACHE", "true"),
    ("AGENT_VAULT_CACHE_DIR", ""), ("AGENT_VAULT_CACHE_DIR", "relative"),
])
def test_pin_never_falls_back_to_unscoped_cache(isolated, monkeypatch, variable, value):
    context = identity_key.source_context()
    for name, item in context["env"].items():
        monkeypatch.setenv(name, item)
    monkeypatch.setenv(variable, value)
    with pytest.raises(RuntimeError, match="absolute CACHE_DIR"):
        config.resolve_context()


def test_empty_pinned_endpoint_clears_override_without_breaking_discovery(
    isolated, monkeypatch,
):
    context = identity_key.source_context()
    for name, value in context["env"].items():
        monkeypatch.setenv(name, value)
    assert context["env"][config.ENDPOINT_ENV] == ""
    assert cli._discover_endpoint(config.resolve_context(), strict=True) is None


def test_full_context_replaces_conflicting_lifecycle_environment(
    isolated, monkeypatch, tmp_path,
):
    selected = {
        config.PID_ENV: str(tmp_path / "selected.pid"),
        config.LOG_ENV: str(tmp_path / "selected.log"),
        config.SYSTEMD_UNIT_ENV: "selected-vault.service",
        config.TASK_NAME_ENV: "SelectedVault",
        "AGENT_VAULT_WINDOWS_RUN_DIR": str(tmp_path / "selected-windows-run"),
    }
    for name, value in selected.items():
        monkeypatch.setenv(name, value)
    context = identity_key.source_context()
    require_keys = set(context["env"])
    inherited = dict(os.environ)
    for name in selected:
        inherited[name] = "unrelated-installation"
    for name in require_keys:
        inherited.pop(name, None)
    inherited.update(context["env"])
    for name, value in selected.items():
        assert inherited[name] == value
    result = subprocess.run(
        [sys.executable, "-c",
         "import json,os;from agent_vault import config;"
         "print(json.dumps({'pid':config.PID_FILE,'log':str(config.LOG_FILE),"
         "'unit':os.environ[config.SYSTEMD_UNIT_ENV],"
         "'task':os.environ[config.TASK_NAME_ENV],"
         "'windows_run':os.environ['AGENT_VAULT_WINDOWS_RUN_DIR']}))"],
        env=inherited, cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "pid": selected[config.PID_ENV], "log": selected[config.LOG_ENV],
        "unit": selected[config.SYSTEMD_UNIT_ENV], "task": selected[config.TASK_NAME_ENV],
        "windows_run": selected["AGENT_VAULT_WINDOWS_RUN_DIR"],
    }


def test_context_lifecycle_defaults_are_absolute_or_legitimately_empty(
    isolated, monkeypatch,
):
    for name in (config.SYSTEMD_UNIT_ENV, config.TASK_NAME_ENV, "AGENT_VAULT_WINDOWS_RUN_DIR"):
        monkeypatch.delenv(name, raising=False)
    environment = identity_key.source_context()["env"]
    assert Path(environment[config.PID_ENV]).is_absolute()
    assert Path(environment[config.LOG_ENV]).is_absolute()
    for name in (config.SYSTEMD_UNIT_ENV, config.TASK_NAME_ENV, "AGENT_VAULT_WINDOWS_RUN_DIR"):
        assert environment[name] == ""


@usable_cache
def test_require_running_stopped_service_never_activates(isolated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("delegated activation"))
    monkeypatch.setattr(cli, "start_service", lambda *args: pytest.fail("spawned resident"))
    calls = []

    def send(request, **kwargs):
        calls.append((request, kwargs))
        return None

    monkeypatch.setattr(cli, "send_command", send)
    assert cli.main(["identity-key", "setup", "--require-running", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "service_unavailable"
    assert calls == [({"action": "ping"}, {"timeout": 5.0, "strict_endpoint": True})]


@usable_cache
def test_require_running_existing_service_populates_without_activation(
    isolated, monkeypatch, capsys,
):
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("delegated activation"))

    def send(request, **kwargs):
        if request["action"] == "ping":
            return {"ok": True}
        return {"ok": True, "status": "reused", "value": KEY, "generation": 10}

    monkeypatch.setattr(cli, "send_command", send)
    assert cli.main(["identity-key", "setup", "--require-running", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": 1, "status": "reused", "cached": True}
