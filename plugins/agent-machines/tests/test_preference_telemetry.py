"""Fixture-resolved peers retain actual producer parsers, locks and writers."""

import json
import os
from pathlib import Path
import sys

import pytest

from agent_machines import __main__ as cli, _peer_launch, configuration, preference_telemetry as adapter
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


@pytest.fixture
def projected_peer(configured, monkeypatch):
    state, target, directory = configured
    plugins = Path(__file__).resolve().parents[2]

    def context(producer):
        return {
            "pluginId": producer, "installReceipt": str(state / "receipt.json"),
            "payloadRoot": str(plugins / producer), "pluginRoot": str(target.parent),
            "marketplaceId": "example-marketplace", "logsRoot": str(target.parent / "logs"),
        }

    def prefix(producer):
        return [
            sys.executable, "-c",
            "import json,os,runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
            "from agent_machines._peer_launch import peer_environment; "
            "environment=peer_environment(json.loads(sys.argv.pop(1)),dict(os.environ)); "
            "os.environ.clear(); os.environ.update(environment); "
            "sys.path.insert(0,sys.argv.pop(1)); "
            "runpy.run_module(sys.argv.pop(1),run_name='__main__')",
            str(plugins / "agent-machines" / "src"), json.dumps(context(producer)),
            str(plugins / producer / "src"), producer.replace("-", "_"),
        ]

    monkeypatch.setattr(adapter, "_prefix", prefix)
    return context


def test_real_peer_projection_reaches_native_writer(configured, projected_peer):
    state, target, directory = configured
    arguments = dict(producer="agent-dispatch", directory=directory)
    preview = adapter.adopt(state, **arguments)
    result = adapter.adopt(state, apply=True, expected=preview["preview_token"], **arguments)
    assert result["configuration_applied"] and result["changed"]
    assert json.loads(target.read_bytes())["retained_spool"]["directory"] == str(directory)
    assert list((directory / "retained").iterdir()) == []


@pytest.mark.parametrize("producer,key,reason", [
    ("agent-dispatch", f"AGENT_DISPATCH_TASK_{suffix}", "task-authority-preserved")
    for suffix in ("TOKEN", "ID", "URL", "WORKER_ID")
] + [
    (producer, producer.upper().replace("-", "_") + "_TELEMETRY_SINK", "environment-sink-preserved")
    for producer in adapter.PRODUCERS
])
def test_restrictions_are_refused_before_peer_selection(
    configured, projected_peer, monkeypatch, producer, key, reason,
):
    state, target, directory = configured
    monkeypatch.setenv(key, "example-restriction")
    assert key not in _peer_launch.peer_environment(projected_peer(producer), dict(os.environ))
    monkeypatch.setattr(adapter, "_prefix", lambda *_args: pytest.fail("restricted peer was selected"))
    with pytest.raises(PreferenceError, match=reason):
        adapter.adopt(state, producer=producer, directory=directory)
    assert not target.exists() and not target.with_suffix(".json.lock").exists()


@pytest.mark.parametrize("producer,key,reason", [
    ("agent-dispatch", "AGENT_DISPATCH_TASK_TOKEN", "task-authority-preserved"),
    ("agent-dispatch", "AGENT_DISPATCH_TELEMETRY_SINK", "environment-sink-preserved"),
])
@pytest.mark.parametrize("handoff", ["capabilities", "preview", "apply"])
def test_changed_environment_is_refused_at_each_peer_handoff(
    configured, projected_peer, monkeypatch, producer, key, reason, handoff,
):
    state, target, directory = configured
    arguments = dict(producer=producer, directory=directory)
    preview = adapter.adopt(state, **arguments)
    original_call, original_run = adapter._call, adapter.run_caller_owned
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return original_run(*args, **kwargs)

    def call(prefix, native_arguments, **fields):
        phase = (
            "capabilities" if native_arguments == ["capabilities"]
            else "apply" if "--apply" in native_arguments else "preview"
        )
        if phase == handoff:
            monkeypatch.setenv(key, "example-restriction")
            assert key not in _peer_launch.peer_environment(projected_peer(producer), dict(os.environ))
        return original_call(prefix, native_arguments, **fields)

    monkeypatch.setattr(adapter, "run_caller_owned", run)
    monkeypatch.setattr(adapter, "_call", call)
    with pytest.raises(PreferenceError, match=reason):
        adapter.adopt(state, apply=True, expected=preview["preview_token"], **arguments)
    assert len(calls) == {"capabilities": 0, "preview": 1, "apply": 2}[handoff]
    assert not target.exists() and not target.with_suffix(".json.lock").exists()


@pytest.mark.parametrize("arguments", [
    ["capabilities"], ["retained"], ["retained", "--apply"],
])
def test_bridge_sink_is_refused_before_projected_handoff(
    configured, projected_peer, monkeypatch, arguments,
):
    _state, target, _directory = configured
    key = "AGENT_BRIDGE_TELEMETRY_SINK"
    monkeypatch.setenv(key, "custom.module:factory")
    assert key not in _peer_launch.peer_environment(projected_peer("agent-bridge"), dict(os.environ))
    monkeypatch.setattr(
        adapter, "run_caller_owned", lambda *_args, **_kwargs: pytest.fail("restricted peer was launched"),
    )
    with pytest.raises(PreferenceError, match="environment-sink-preserved"):
        adapter._call([], arguments, producer="agent-bridge")
    assert not target.exists() and not target.with_suffix(".json.lock").exists()


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


@pytest.mark.parametrize("failure,exit_code,status", [
    ("missing-retained", 2, "error"), ("custom-sink", 1, "refused"),
])
def test_cli_distinguishes_invalid_request_from_preservation_refusal(
    configured, capsys, failure, exit_code, status,
):
    state, target, directory = configured
    if failure == "missing-retained":
        (directory / "retained").rmdir()
    else:
        target.write_text('{"sink":"custom.module:factory"}', encoding="utf-8")
    before = target.read_bytes() if target.exists() else None
    assert cli.main([
        "preferences", "telemetry", "--state-dir", str(state), "--producer", "agent-dispatch",
        "--directory", str(directory),
    ]) == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and not result["configuration_applied"]
    assert (target.read_bytes() if target.exists() else None) == before
    assert not target.with_suffix(".json.lock").exists()


def test_local_override_cannot_be_the_native_configuration_target(configured):
    state, target, directory = configured
    original = json.dumps({
        "schema": "agent-machines.preferences.local", "version": 1,
        "machine": "example-worker", "domains": {},
    }).encode("utf-8")
    target.write_bytes(original)
    arguments = dict(producer="agent-dispatch", directory=directory, local=target)
    preview = adapter.adopt(state, **arguments)
    assert preview["status"] == "error" and not preview["eligible"]
    assert "overlaps native output" in preview["native"]["reason"]
    result = adapter.adopt(state, apply=True, expected=preview["preview_token"], **arguments)
    assert not result["configuration_applied"]
    assert target.read_bytes() == original and not target.with_suffix(".json.lock").exists()


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


def test_unknown_native_acknowledgement_is_not_reported_as_untouched(configured, monkeypatch, capsys):
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
    assert cli.main([
        "preferences", "telemetry", "--state-dir", str(state), "--producer", "agent-dispatch",
        "--directory", str(directory), "--apply", "--expect", preview["preview_token"],
    ]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unknown" and result["configuration_applied"] is None
    assert len(applies) == 1
    assert json.loads(target.read_bytes())["retained_spool"]["directory"] == str(directory)
