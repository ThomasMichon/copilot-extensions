"""Payload recovery retries failed targets, never a policy refusal or peer."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import (
    __main__ as core, activation_preservation, reconcile, update_acquisition, update_events,
)

pytestmark = pytest.mark.guard


@pytest.fixture
def acquisition(monkeypatch):
    monkeypatch.setattr(core, "_resolve_copilot", lambda: "copilot")
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: Path("/payload") / name)
    calls = []
    responses = []
    def run(argv, **kwargs):
        calls.append(list(argv))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(argv, result[0], "", result[1])
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(activation_preservation, "run_install_preserving_activation",
                        lambda argv, identity, **kwargs: run(argv, **kwargs))
    return calls, responses


@pytest.mark.parametrize("detail", [
    "HTTP 401 authentication required",
    "HTTP 403 permission denied",
    "EACCES permission denied",
    "sha256 checksum mismatch",
    "source policy refused",
    "certificate verification failed",
    "ECONNRESET after signature verification failed",
])
def test_authority_integrity_and_unknown_failures_never_trigger_install(acquisition, detail):
    calls, responses = acquisition
    responses.extend([(1, detail), (0, "")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert not status.startswith("OK")
    assert len(calls) == 1


def test_transient_failure_retries_the_same_verb(acquisition):
    calls, responses = acquisition
    responses.extend([(1, "ECONNRESET"), (0, "")])
    assert core._update_one_plugin_payload("agent-example", "copilot-extensions") == "OK"
    assert [argv[2] for argv in calls] == ["update", "update"]


def test_unknown_failure_is_not_a_missing_ledger(acquisition):
    calls, responses = acquisition
    responses.extend([(7, "unexpected deterministic failure"), (0, "")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert not status.startswith("OK")
    assert "unexpected deterministic failure" in status
    assert len(calls) == 1


def test_timeout_never_launches_an_overlapping_retry(acquisition):
    calls, responses = acquisition
    responses.extend([subprocess.TimeoutExpired("copilot", 120), (0, "")])
    assert core._update_one_plugin_payload("agent-example", "copilot-extensions") == "timed out"
    assert len(calls) == 1


def test_exhaustion_is_bounded_and_retains_decisive_failure(acquisition, monkeypatch):
    calls, responses = acquisition
    waits = []
    monkeypatch.setattr(update_acquisition, "sleep", waits.append)
    responses.extend([(1, "ECONNRESET"), (8, "HTTP 503 unavailable"), (0, "")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert "HTTP 503" in status and "exit 8" in status
    assert len(calls) == 2
    assert waits == [1]


def test_retry_reinspects_payload_and_preserves_install_activation(acquisition, monkeypatch):
    calls, responses = acquisition
    inspected = []
    def payload(name):
        inspected.append(name)
        return Path("/payload") if len(inspected) == 1 else None
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", payload)
    responses.extend([(1, "ECONNRESET"), (0, "")])
    assert core._update_one_plugin_payload("agent-example", "copilot-extensions") == "OK (installed)"
    assert inspected == ["agent-example", "agent-example"]
    assert [argv[2] for argv in calls] == ["update", "install"]


def test_missing_ledger_repair_failure_keeps_final_cause(acquisition):
    calls, responses = acquisition
    responses.extend([
        (1, "Plugin agent-example is not installed"),
        (4, "source policy refused"),
    ])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert "source policy refused" in status and "exit 4" in status
    assert [argv[2] for argv in calls] == ["update", "install"]


def test_engine_retry_never_changes_to_install(acquisition):
    calls, responses = acquisition
    responses.extend([(1, "ECONNRESET"), (0, "")])
    assert update_acquisition.acquire(
        "agent-worktrees", "copilot-extensions", "copilot", engine=True,
    ) == "OK"
    assert [argv[2] for argv in calls] == ["update", "update"]


def test_engine_missing_ledger_is_not_install_authorization(acquisition):
    calls, responses = acquisition
    responses.extend([(1, "Plugin agent-worktrees is not installed"), (0, "")])
    assert not update_acquisition.acquire(
        "agent-worktrees", "copilot-extensions", "copilot", engine=True,
    ).startswith("OK")
    assert len(calls) == 1


def test_another_plugins_ledger_error_cannot_authorize_install(acquisition):
    calls, responses = acquisition
    responses.extend([(1, "Plugin another-plugin is not installed"), (0, "")])
    assert not core._update_one_plugin_payload("agent-example", "copilot-extensions").startswith("OK")
    assert len(calls) == 1


def test_long_diagnostic_cannot_erase_refusal_reason(acquisition):
    calls, responses = acquisition
    responses.extend([(1, "signature verification failed\n" + "context " * 80 + "ECONNRESET")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert "refused (signature," in status
    assert len(calls) == 1


@pytest.mark.parametrize("code,attempts", [(32, 2), (33, 2), (5, 1)])
def test_only_explicit_spawn_contention_can_retry(acquisition, code, attempts):
    calls, responses = acquisition
    error = OSError("synthetic spawn failure")
    error.winerror = code
    responses.extend([error, (0, "")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert status.startswith("OK") is (attempts == 2)
    assert len(calls) == attempts


@pytest.mark.parametrize("detail,windows", [
    ("[WinError 5] localized refusal", False),
    ("[WinError 5] localized refusal", True),
    ("[Errno 1] localized refusal", False),
    ("[Errno 13] localized refusal", True),
    ("(os error 5) localized refusal", True),
    ("(os error 1) localized refusal", False),
    ("(os error 13) localized refusal", False),
    ("(os error 5) localized I/O failure", False),
])
@pytest.mark.parametrize("recovery", ["ECONNRESET", "Plugin agent-example is not installed"])
def test_numeric_refusals_stop_both_recovery_paths(
    acquisition, monkeypatch, detail, windows, recovery,
):
    calls, responses = acquisition
    monkeypatch.setattr(update_events, "_WINDOWS", windows)
    responses.extend([(1, f"{detail}\n{recovery}"), (0, "")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert not status.startswith("OK")
    assert len(calls) == 1


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("code", [32, 33])
def test_native_os_error_sharing_is_host_aware(acquisition, monkeypatch, windows, code):
    calls, responses = acquisition
    monkeypatch.setattr(update_events, "_WINDOWS", windows)
    responses.extend([(1, f"localized failure (os error {code})"), (0, "")])
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    assert status.startswith("OK") is windows
    assert len(calls) == (2 if windows else 1)


@pytest.mark.parametrize("command_timeout", [False, True])
@pytest.mark.parametrize("recover", [False, True])
def test_real_install_restoration_never_replays_the_command(
    tmp_path, monkeypatch, command_timeout, recover,
):
    from plugin_activation import state

    home, payload = tmp_path / "copilot", tmp_path / "payload"
    home.mkdir()
    identity = "agent-example@copilot-extensions"
    settings = home / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {identity: False}}))
    monkeypatch.setattr(state, "copilot_home", lambda: home)
    monkeypatch.setattr(
        activation_preservation, "run_install_preserving_activation",
        state.run_install_preserving_activation,
    )
    monkeypatch.setattr(core, "_resolve_copilot", lambda: "copilot")
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(state, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir",
        lambda name: payload if payload.is_dir() else None,
    )
    calls = []
    def install(argv, **kwargs):
        calls.append(list(argv))
        if argv[2] == "install":
            payload.mkdir()
            settings.write_text(json.dumps({"enabledPlugins": {identity: True}}))
            if command_timeout:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(subprocess, "run", install)
    replace, writes = state.os.replace, []
    def fail_restore(source, destination):
        if Path(destination) == settings:
            writes.append(destination)
            if not recover or len(writes) == 1:
                error = OSError("activation restoration write blocked")
                error.winerror = 32
                raise error
        return replace(source, destination)
    monkeypatch.setattr(state.os, "replace", fail_restore)
    status = core._update_one_plugin_payload("agent-example", "copilot-extensions")
    if recover:
        assert status == ("timed out" if command_timeout else "OK (installed)")
    else:
        assert not status.startswith("OK")
        assert "restor" in status
    assert len(calls) == 1
    assert len(writes) == 2
    assert json.loads(settings.read_text())["enabledPlugins"][identity] is (not recover)


def test_retry_timeout_uses_remaining_total_budget(acquisition, monkeypatch):
    calls, responses = acquisition
    clock = iter([0.0, 0.0, 239.5])
    monkeypatch.setattr(update_acquisition, "monotonic", lambda: next(clock))
    timeouts = []
    def run(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 1 if len(timeouts) == 1 else 0, "", "ECONNRESET")
    monkeypatch.setattr(subprocess, "run", run)
    assert update_acquisition.acquire(
        "agent-worktrees", "copilot-extensions", "copilot", engine=True,
    ) == "OK"
    assert timeouts == [120, 0.5]


def test_real_failed_target_retry_never_reacquires_successful_peer(tmp_path, monkeypatch):
    from agent_worktrees import update_runtime

    monkeypatch.setattr(core, "_resolve_copilot", lambda: "copilot")
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(core, "_refresh_marketplace", lambda *a, **k: True)
    monkeypatch.setattr(core, "_browse_marketplace_plugins", lambda *a, **k: None)
    run = subprocess.run
    program = (
        "import json,pathlib,sys;"
        "p=pathlib.Path(sys.argv[1]);"
        "d=json.loads(p.read_text()) if p.exists() else {};"
        "n=sys.argv[2];d[n]=d.get(n,0)+1;p.write_text(json.dumps(d));"
        "fail=n=='b' and d[n]==1;"
        "print('ECONNRESET' if fail else 'acquired',file=sys.stderr);"
        "sys.exit(1 if fail else 0)"
    )
    def child(argv, **kwargs):
        name = argv[-1].split("@", 1)[0]
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return run([sys.executable, "-c", program, str(tmp_path / "attempts.json"), name], **kwargs)
    monkeypatch.setattr(subprocess, "run", child)
    target = update_runtime._RegisteredPluginTarget(
        tmp_path, update_runtime._PluginActivation.ACTIVE,
    )
    assert update_runtime._update_registered_plugins({name: target for name in ("a", "b", "c")})
    assert json.loads((tmp_path / "attempts.json").read_text()) == {"a": 1, "b": 2, "c": 1}
