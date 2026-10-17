"""Installation-local setup and the real fresh-process consumer boundary."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys

import pytest

from agent_worktrees import identity_marker, identity_vault, identity_vault_cli as cli
from agent_worktrees import identity_vault_settings as settings


@pytest.fixture
def source(monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    for key in (identity_marker.KEY_ENV_VAR, identity_vault.ARGV_ENV, identity_vault.ENTRY_ENV):
        monkeypatch.delenv(key, raising=False)
    adapter = tmp_path / "vault.py"
    env = {
        "COPILOT_EXTENSIONS_CONTEXT": "",
        "AGENT_VAULT_PINNED_CONTEXT": json.dumps({
            "version": 1, "kpdb": str(tmp_path / "owner.kdbx"),
            "group": "", "vault_name": "", "port": 19999,
        }),
        "AGENT_VAULT_CACHE": "1", "AGENT_VAULT_CACHE_DIR": str(tmp_path / "protected"),
    }
    return {"version": 1, "argv": [sys.executable, str(adapter)], "entry": "Identity/PR-attribution", "env": env}


def args(verb, **kwargs):
    return argparse.Namespace(
        identity_vault_command=verb, json=True, vault_argv=None, vault_context=None, entry=None,
        prompt=False, initialize_owner=False, confirm_first_owner=False, **kwargs,
    )


def test_persisted_source_pins_cache_and_ignores_ambient_vault(source, monkeypatch):
    settings.save_settings(source)
    monkeypatch.setenv("KPDB", "wrong-repository.kdbx")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", "wrong-cache")
    calls = []

    def read(argv, *, env, timeout):
        assert timeout == identity_vault._PAYLOAD_READ_TIMEOUT
        calls.append((argv, env))
        return base64.b64encode(b"s" * 32)

    monkeypatch.setattr(identity_vault, "_read_key_output", read)
    assert identity_marker.load_identity_key() == b"s" * 32
    assert calls[0][0][-4:] == ["get", source["entry"], "password", "--cache-only"]
    assert calls[0][1]["AGENT_VAULT_CACHE_DIR"] == source["env"]["AGENT_VAULT_CACHE_DIR"]
    assert "KPDB" not in calls[0][1]


def test_configured_failure_never_uses_legacy_file(source, monkeypatch, caplog):
    settings.save_settings(source)
    settings.settings_path().write_text("not-json")
    monkeypatch.setattr(identity_marker, "default_identity_key_path", lambda: pytest.fail("legacy fallback"))
    assert identity_marker.load_identity_key() is None
    assert "shared vault key unavailable" in caplog.text
    with pytest.raises(identity_marker.IdentityMarkerError, match="one shared owner key"):
        identity_marker.generate_identity_key()


def test_explicit_file_and_environment_precedence(source, monkeypatch, tmp_path):
    settings.save_settings(source)
    file = tmp_path / "legacy.key"
    file.write_bytes(base64.b64encode(b"f" * 32))
    monkeypatch.setenv(identity_marker.KEY_ENV_VAR, str(file))
    assert identity_marker.load_identity_key() == b"f" * 32
    monkeypatch.delenv(identity_marker.KEY_ENV_VAR)
    monkeypatch.setenv(identity_vault.ARGV_ENV, "invalid")
    assert identity_marker.load_identity_key() is None


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(version=True),
    lambda value: value.update(argv=["relative-command"]),
    lambda value: value.update(entry="bare"),
    lambda value: value["env"].update(SECRET="not-allowed"),
    lambda value: value["env"].update(AGENT_VAULT_CACHE="0"),
    lambda value: value["env"].update(AGENT_VAULT_CACHE_DIR="relative-cache"),
])
def test_invalid_configuration_refused_without_files(source, mutate):
    mutate(source)
    with pytest.raises(ValueError):
        settings.save_settings(source)
    assert not settings.settings_path().exists()


def test_oversized_valid_strings_cannot_persist_unreadable_settings(source):
    source["argv"] = [sys.executable] + ["x" * 4096] * 15
    with pytest.raises(ValueError, match="oversized"):
        settings.save_settings(source)
    assert not settings.settings_path().exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launcher argument boundary")
@pytest.mark.parametrize("character", list('&|<>^%!"'))
def test_shell_sensitive_entry_refused_before_invocation(source, character):
    source["entry"] = "Identity/key" + character + "suffix"
    with pytest.raises(ValueError, match="safe for the owning launcher"):
        settings.save_settings(source)
    assert not settings.settings_path().exists()


def test_status_is_read_only_and_secret_free(source, monkeypatch, capsys):
    settings.save_settings(source)
    before = {p: p.read_bytes() for p in settings.settings_path().parent.rglob("*") if p.is_file()}
    monkeypatch.setattr(cli, "_cache_ready", lambda selected: True)
    monkeypatch.setattr(cli, "_vault_json", lambda *a, **k: pytest.fail("status contacted live vault"))
    assert cli.cmd_identity_vault(args("status")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ready"] is True
    assert "env" not in result and "key" not in result
    assert before == {p: p.read_bytes() for p in settings.settings_path().parent.rglob("*") if p.is_file()}


@pytest.mark.parametrize("error", [ValueError("SECRET-BACKEND-ERROR"), OSError("SECRET-BACKEND-ERROR")])
def test_failed_setup_preserves_previous_config(source, monkeypatch, capsys, error):
    settings.save_settings(source)
    before = settings.settings_path().read_bytes()

    def fail(*a, **k):
        raise error

    monkeypatch.setattr(cli, "_vault_json", fail)
    assert cli.cmd_identity_vault(args("setup")) == 1
    assert settings.settings_path().read_bytes() == before
    assert "SECRET" not in capsys.readouterr().err


def test_setup_delegates_population_and_verifies_before_commit(source, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_candidate", lambda args: source)

    def vault(argv, argv_tail, **kwargs):
        calls.append((argv, argv_tail, kwargs))
        return {"version": 1, "status": "reused", "cached": True}

    monkeypatch.setattr(cli, "_vault_json", vault)
    monkeypatch.setattr(cli, "_fresh_probe", lambda selected: calls.append("verified"))
    assert cli.cmd_identity_vault(args("setup")) == 0
    assert settings.load_settings() == source
    assert calls[0][1] == ["identity-key", "setup", "--entry", source["entry"], "--require-running", "--json"]
    assert calls[-1] == "verified"
    assert json.loads(capsys.readouterr().out)["key"] == "reused"


@pytest.mark.parametrize("flag", ["initialize_owner", "confirm_first_owner"])
def test_initialization_requires_both_flags(source, monkeypatch, flag):
    monkeypatch.setattr(cli, "_candidate", lambda args: source)
    monkeypatch.setattr(cli, "_vault_json", lambda *a, **k: pytest.fail("unconfirmed initialization"))
    invocation = args("setup")
    setattr(invocation, flag, True)
    assert cli.cmd_identity_vault(invocation) == 1
    assert not settings.settings_path().exists()


def test_conflicting_overrides_block_setup_before_population(source, monkeypatch):
    monkeypatch.setattr(cli, "_candidate", lambda args: source)
    monkeypatch.setenv(identity_marker.KEY_ENV_VAR, "existing-file")
    monkeypatch.setattr(cli, "_vault_json", lambda *a, **k: pytest.fail("must not provision"))
    assert cli.cmd_identity_vault(args("setup")) == 1
    assert not settings.settings_path().exists()


def test_real_fresh_process_uses_persisted_source_without_vault_unlock(source, monkeypatch, tmp_path):
    adapter = tmp_path / "vault.py"
    adapter.write_text(
        "import os,sys,base64\n"
        "assert sys.argv[1:] == ['get','Identity/PR-attribution','password','--cache-only']\n"
        "assert os.environ['AGENT_VAULT_CACHE']=='1'\n"
        "assert os.path.isabs(os.environ['AGENT_VAULT_CACHE_DIR'])\n"
        "assert 'KPDB' not in os.environ\n"
        "print(base64.b64encode(b's'*32).decode())\n",
    )
    settings.save_settings(source)
    monkeypatch.setenv("KPDB", "wrong-ambient.kdbx")
    assert cli._fresh_probe() == {"version": 1, "ready": True, "cache_only": True, "roundtrip": True}


def test_front_door_without_project_has_real_status(source, tmp_path):
    env = os.environ.copy()
    env.pop("WORKTREE_PROJECT", None)
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "identity-vault", "status", "--json"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["configured"] is False


def test_nonzero_adapter_cannot_claim_success(source, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_candidate", lambda args: source)
    monkeypatch.setattr(
        identity_vault, "_read_command_output",
        lambda *a, **k: (1, b'{"version":1,"status":"created","cached":true}'),
    )
    assert cli.cmd_identity_vault(args("setup")) == 1
    assert not settings.settings_path().exists()
    assert "created" not in capsys.readouterr().err


@pytest.mark.parametrize("code", list(cli._VAULT_ACTIONS))
def test_backend_failure_codes_remain_actionable_without_echoing_payload(source, monkeypatch, capsys, code):
    monkeypatch.setattr(cli, "_candidate", lambda args: source)
    monkeypatch.setattr(
        identity_vault, "_read_command_output",
        lambda *a, **k: (1, json.dumps({
            "version": 1, "error": code, "message": "SECRET-BACKEND-DIAGNOSTIC",
        }).encode()),
    )
    assert cli.cmd_identity_vault(args("setup")) == 1
    error = capsys.readouterr().err
    assert json.loads(error)["error"] == code
    assert "SECRET" not in error
    assert not settings.settings_path().exists()


def test_malformed_adapter_status_or_error_never_escapes_sanitization(source, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_candidate", lambda args: source)
    monkeypatch.setattr(
        identity_vault, "_read_command_output",
        lambda *a, **k: (0, b'{"version":1,"status":{"SECRET":"payload"},"cached":true}'),
    )
    assert cli.cmd_identity_vault(args("setup")) == 1
    assert "SECRET" not in capsys.readouterr().err
    monkeypatch.setattr(
        identity_vault, "_read_command_output",
        lambda *a, **k: (1, b'{"version":1,"error":{"SECRET":"payload"}}'),
    )
    assert cli.cmd_identity_vault(args("setup")) == 1
    failure = capsys.readouterr().err
    assert "SECRET" not in failure
    assert json.loads(failure)["error"] == "invalid_response"
    assert not settings.settings_path().exists()


def test_vault_discovery_and_reads_do_not_inherit_worktrees_context(source, monkeypatch, tmp_path):
    worktrees = str(tmp_path / "worktrees" / "install.json")
    vault = str(tmp_path / "vault" / "install.json")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", worktrees)
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "foreign-worktrees-payload")
    assert "COPILOT_EXTENSIONS_CONTEXT" not in settings.discovery_environment()
    assert "COPILOT_PLUGIN_ROOT" not in settings.discovery_environment()
    assert settings.discovery_environment()["AGENT_VAULT_NO_SELFPROVISION"] == "1"
    assert settings.discovery_environment()["COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH"] == os.devnull
    assert settings.discovery_environment(vault)["COPILOT_EXTENSIONS_CONTEXT"] == vault
    source["env"]["COPILOT_EXTENSIONS_CONTEXT"] = vault
    selected = settings.source_environment(source)
    assert selected["COPILOT_EXTENSIONS_CONTEXT"] == vault
    assert "COPILOT_PLUGIN_ROOT" not in selected
    assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == worktrees


def test_candidate_probe_keeps_caller_receipt_and_uses_bounded_stdin(source, monkeypatch, tmp_path):
    worktrees = str(tmp_path / "worktrees" / "install.json")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", worktrees)
    captured = {}

    def probe(argv, tail, **kwargs):
        captured.update(kwargs)
        assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == worktrees
        return {"version": 1, "ready": True, "cache_only": True, "roundtrip": True}

    monkeypatch.setattr(cli, "_vault_json", probe)
    assert cli._fresh_probe(source)["ready"] is True
    assert captured.get("env") is None
    assert json.loads(captured["input_data"]) == source


def test_real_bounded_probe_input_reaches_child_without_environment_replacement(source):
    status, output = identity_vault._read_command_output(
        [sys.executable, "-c", "import sys;data=sys.stdin.buffer.read();print(len(data))"],
        input_data=json.dumps(source).encode(),
    )
    assert status == 0
    assert int(output) == len(json.dumps(source).encode())


def test_blocked_probe_input_is_contained_and_times_out():
    import time

    started = time.monotonic()
    with pytest.raises(ValueError, match="timed out"):
        identity_vault._read_command_output(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            input_data=b"x" * 32768, timeout=0.2,
        )
    assert time.monotonic() - started < 10
