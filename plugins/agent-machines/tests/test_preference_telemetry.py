"""Fixture-resolved peers retain actual producer parsers, locks and writers."""

import json
from pathlib import Path
import sys

import pytest

from agent_machines import __main__ as cli, configuration, preference_telemetry as adapter
from agent_machines.preferences import PreferenceError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    state, native, directory = (tmp_path / name for name in ("state", "native", "spool"))
    state.mkdir()
    native.mkdir()
    (directory / "retained").mkdir(parents=True)
    (state / "policy.json").write_text(json.dumps({
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-receiver", "domains": ["telemetry"],
    }), encoding="utf-8")
    (state / "accepted.json").write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"telemetry": {"capture": "retained-local", "reporting": "local"}},
    }), encoding="utf-8")
    context = {
        "repository_id": "repo-example", "execution_machine": "example-worker",
        "installation_cell": "example-cell", "binding": "unbound", "base_root": str(tmp_path),
    }
    monkeypatch.setattr(configuration, "owner_report", lambda root: {
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
        "repository_machine_root": str(tmp_path),
    })
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT", "AGENT_BRIDGE_TELEMETRY_SINK",
                "AGENT_DISPATCH_TELEMETRY_SINK"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(native))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(native))
    plugins = Path(__file__).resolve().parents[2]

    def prefix(producer):
        source = plugins / producer / "src"
        assert source.is_dir(), "native producer source is required, not a fake response"
        return [
            sys.executable, "-c",
            "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
            "runpy.run_module(sys.argv.pop(1),run_name='__main__')",
            str(source), producer.replace("-", "_"),
        ]

    monkeypatch.setattr(adapter, "_prefix", prefix)
    return state, native / "telemetry.json", directory


def test_normal_cli_reaches_actual_producer_without_activation(configured, capsys):
    state, target, directory = configured
    arguments = [
        "preferences", "telemetry", "--state-dir", str(state), "--producer", "agent-dispatch",
        "--directory", str(directory),
    ]
    assert cli.main(arguments) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not target.exists() and not target.with_suffix(".json.lock").exists()
    assert preview["reporting_adoption"] == "unsupported"
    assert cli.main(arguments + ["--apply", "--expect", preview["preview_token"]]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["configuration_applied"] and result["changed"]
    assert result["activation"] == "not-requested" and result["running_capture"] == "not-observed"
    assert json.loads(target.read_bytes()) == {"retained_spool": {"directory": str(directory)}}
    assert list((directory / "retained").iterdir()) == []


@pytest.mark.parametrize("changed", ["policy.json", "accepted.json", "local.json"])
def test_parent_check_cannot_authorize_changed_inputs_at_producer(configured, tmp_path, monkeypatch, changed):
    state, target, directory = configured
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "schema": "agent-machines.preferences.local", "version": 1,
        "machine": "example-worker", "domains": {},
    }), encoding="utf-8")
    args = dict(producer="agent-dispatch", directory=directory, local=local)
    preview = adapter.adopt(state, **args)
    original = adapter._call

    def call(prefix, arguments, **fields):
        if "--apply" in arguments:
            path = local if changed == "local.json" else state / changed
            path.write_bytes(path.read_bytes() + b"\n")
        return original(prefix, arguments, **fields)

    monkeypatch.setattr(adapter, "_call", call)
    result = adapter.adopt(state, apply=True, expected=preview["preview_token"], **args)
    assert result["status"] == "refused" and not result["configuration_applied"]
    assert "configuration input changed" in result["native"]["reason"]
    assert not target.exists() and not target.with_suffix(".json.lock").exists()


def test_custom_sink_and_withdrawal_preserve_configuration(configured, monkeypatch):
    state, target, directory = configured
    original = b'{"sink":"custom.module:factory","other":true}'
    target.write_bytes(original)
    args = dict(producer="agent-dispatch", directory=directory)
    result = adapter.adopt(state, **args)
    assert result["status"] == "refused" and not result["eligible"]
    assert target.read_bytes() == original
    accepted = json.loads((state / "accepted.json").read_bytes())
    accepted.update(revision=2, parent=1, domains={})
    (state / "accepted.json").write_text(json.dumps(accepted), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        pytest.fail("withdrawal contacted or mutated a producer")

    monkeypatch.setattr(adapter, "_prefix", forbidden)
    result = adapter.adopt(state, **args)
    applied = adapter.adopt(state, apply=True, expected=result["preview_token"], **args)
    assert applied["status"] == "no-contribution" and not applied["configuration_applied"]
    assert target.read_bytes() == original


def test_local_disabled_override_and_guest_scope_do_not_enable_capture(configured, tmp_path, monkeypatch):
    state, target, directory = configured
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "schema": "agent-machines.preferences.local", "version": 1,
        "machine": "example-worker", "domains": {"telemetry": {"capture": "disabled"}},
    }), encoding="utf-8")
    monkeypatch.setattr(adapter, "_prefix", lambda *_args: pytest.fail("disabled default contacted producer"))
    result = adapter.adopt(state, producer="agent-bridge", directory=directory, local=local)
    assert result["status"] == "no-contribution" and not target.exists()
    document = json.loads(local.read_bytes())
    document["machine"] = "example-worker-wsl"
    local.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PreferenceError, match="local-machine-mismatch"):
        adapter.adopt(state, producer="agent-bridge", directory=directory, local=local)


def test_unsupported_old_contract_never_tries_native_apply(configured, monkeypatch):
    state, target, directory = configured
    response = {
        "schema": adapter.NATIVE_SCHEMA, "operations": ["preview", "apply"],
        "configuration_only": True, "custom_sink_overwrite": False,
    }
    monkeypatch.setattr(adapter, "_prefix", lambda *_args: [
        sys.executable, "-c", f"print({json.dumps(json.dumps(response))})",
    ])
    with pytest.raises(PreferenceError, match="native-contract-unsupported"):
        adapter.adopt(state, producer="agent-bridge", directory=directory)
    assert not target.exists() and not target.with_suffix(".json.lock").exists()


@pytest.mark.parametrize("fields", [{"status": []}, {"status": "preview", "confirmation": None}])
def test_malformed_native_preview_is_not_an_authorization(configured, monkeypatch, fields):
    state, target, directory = configured
    support = {
        "schema": adapter.NATIVE_SCHEMA, "operations": ["preview", "apply"],
        "configuration_only": True, "custom_sink_overwrite": False, "input_fence": 1,
    }
    response = {"schema": adapter.NATIVE_SCHEMA, "activation": "not-requested", **fields}
    script = (
        "import sys; print("
        + repr(json.dumps(support)) + " if sys.argv[2]=='capabilities' else "
        + repr(json.dumps(response)) + ")"
    )
    monkeypatch.setattr(adapter, "_prefix", lambda *_args: [sys.executable, "-c", script])
    with pytest.raises(PreferenceError, match="native-response-invalid"):
        adapter.adopt(state, producer="agent-dispatch", directory=directory)
    assert not target.exists() and not target.with_suffix(".json.lock").exists()


def test_unknown_native_acknowledgement_is_not_reported_as_untouched(configured, monkeypatch):
    state, target, directory = configured
    arguments = dict(producer="agent-dispatch", directory=directory)
    preview = adapter.adopt(state, **arguments)
    original = adapter._call
    applies = []

    def call(prefix, arguments, **fields):
        response = original(prefix, arguments, **fields)
        if "--apply" in arguments:
            applies.append(arguments)
            return {**response, "status": "error", "write_outcome": "unknown"}
        return response

    monkeypatch.setattr(adapter, "_call", call)
    result = adapter.adopt(state, apply=True, expected=preview["preview_token"], **arguments)
    assert result["status"] == "unknown" and result["configuration_applied"] is None
    assert len(applies) == 1
    assert json.loads(target.read_bytes())["retained_spool"]["directory"] == str(directory)
