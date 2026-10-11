from __future__ import annotations

import json
from types import SimpleNamespace

from agent_dispatch.supervisor_lifecycle import Invocation, audit, daemon_audit, main


def test_exact_selection_record_without_environment_or_human_attribution(tmp_path):
    env_file = tmp_path / "example.env"
    env_file.write_text(
        'AGENT_DISPATCH_SUPERVISE_MODE="serve"\n'
        "AGENT_DISPATCH_SUPERVISE_LABELS=example\n"
        "AGENT_DISPATCH_SUPERVISE_MACHINE=host-a\n"
        "AGENT_DISPATCH_TOKEN=not-for-the-journal\n"
        "AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=not-for-the-journal\n"
    )
    invocation = Invocation("installer.sh", "update", "123-456", 123, 100)
    assert audit(
        tmp_path, "selection", "prior-native-disabled", invocation=invocation,
        env_file=env_file, native_enabled="disabled", native_active="inactive",
        unit="agent-dispatch-supervisor.service", version="1.2.3", outcome="skipped",
    )
    raw = (tmp_path / "lifecycle.log").read_text()
    record = json.loads(raw)
    assert record["action"] == "supervisor-selection"
    assert record["outcome"] == "skipped"
    assert record["version"] == "1.2.3"
    assert record["detail"] == {
        "schema": "agent-dispatch.supervisor-lifecycle/v1",
        "attribution": "source-owned-action", "reason": "prior-native-disabled",
        "origin": "installer.sh", "verb": "update", "correlation": "123-456",
        "initiator_pid": 123, "parent_pid": 100,
        "unit": "agent-dispatch-supervisor.service",
        "native_enabled": "disabled", "native_active": "inactive",
        "native_main_pid": None, "selection_observation": "audit-time",
        "native_enablement_provenance": "unknown",
        "desired_enabled": False,
        "env_state": "read", "mode": "serve", "label_opt_in": True,
        "configured_enabled": True, "machine": "host-a",
        "scope_source": "explicit-machine", "profile": "example",
    }
    assert "not-for-the-journal" not in raw
    assert "operator-disabled" not in raw


def test_unreadable_env_is_not_falsely_attributed_to_inert_selection(tmp_path, caplog):
    env_file = tmp_path / "example.env"
    env_file.write_bytes(b"\xff")
    assert audit(
        tmp_path, "stop", "lifecycle-stop",
        invocation=Invocation("installer.sh", "stop", "123-456", 123, 100),
        env_file=env_file,
    )
    detail = json.loads((tmp_path / "lifecycle.log").read_text())["detail"]
    assert detail["env_state"] == "unreadable"
    assert detail["configured_enabled"] is None
    assert detail["desired_enabled"] is None
    assert "selection env is unreadable" in caplog.text


def test_audit_write_failure_is_visible_and_does_not_claim_success(tmp_path, caplog):
    (tmp_path / "lifecycle.log").mkdir()
    invocation = Invocation("installer.ps1", "update", "123-456", 123, 100)
    assert not audit(tmp_path, "restart", "configured-opt-in", invocation=invocation)
    assert "audit unavailable" in caplog.text
    assert "action is not suppressed" in caplog.text
    assert main([
        "--root", str(tmp_path), "--action", "restart", "--reason", "configured-opt-in",
        "--origin", "installer.ps1", "--verb", "update", "--correlation", "123-456",
        "--initiator-pid", "123", "--parent-pid", "100",
    ]) == 1


def test_runtime_scope_and_spawn_request_are_not_successor_readiness(tmp_path):
    daemon = SimpleNamespace(
        _self_update_install_dir=lambda: tmp_path,
        _self_update_running_version="1.2.3", machine="host-a", env="example",
    )
    daemon_audit(daemon, "handoff", "new-slot-selected")
    daemon_audit(daemon, "handoff", "spawn-request-returned", outcome="ok")
    events = [json.loads(line) for line in (tmp_path / "lifecycle.log").read_text().splitlines()]
    assert [event["outcome"] for event in events] == ["begin", "ok"]
    assert events[0]["detail"]["correlation"] == events[1]["detail"]["correlation"]
    assert events[1]["detail"]["scope_source"] == "resolved-runtime"
    assert events[1]["detail"]["machine"] == "host-a"
    assert events[1]["detail"]["environment"] == "example"
    assert "successor_ready" not in events[1]["detail"]


def test_failed_spawn_attempt_has_structured_failure(tmp_path, monkeypatch):
    from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon
    monkeypatch.delenv("AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT", raising=False)

    def fail(*_args):
        raise OSError("fixture spawn failed")

    daemon = _daemon(
        FakeClient([]), FakeLauncher(), lock=FakeLock(granted=True),
        self_update_enabled=True, self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: tmp_path, self_update_running_version="1.0.0",
        self_update_stale_target=lambda *_: tmp_path / "new/bin/python",
        self_update_spawn=fail,
    )
    assert not daemon._maybe_self_update()
    events = [json.loads(line) for line in (tmp_path / "lifecycle.log").read_text().splitlines()]
    assert events[-1]["outcome"] == "fail"
    assert events[-1]["detail"]["reason"] == "spawn-request-returned"


def test_runtime_mask_state_is_retained(tmp_path):
    assert audit(
        tmp_path, "selection", "prior-native-masked",
        invocation=Invocation("installer.sh", "update", "123-456", 123, 100),
        native_enabled="masked-runtime", outcome="skipped",
    )
    detail = json.loads((tmp_path / "lifecycle.log").read_text())["detail"]
    assert detail["native_enabled"] == "masked-runtime"
    assert detail["desired_enabled"] is False


def test_windows_selection_semantics_do_not_claim_posix_equivalence(tmp_path):
    from agent_dispatch.supervisor_lifecycle import selection_facts
    env_file = tmp_path / "example.env"
    env_file.write_text(
        "agent_dispatch_supervise_mode=SERVE\n"
        "AGENT_DISPATCH_SUPERVISE_MODE=legacy\n"
        "AGENT_DISPATCH_SUPERVISE_LABELS=example\n"
        "AGENT_DISPATCH_SUPERVISE_LABELS=\n"
    )
    windows = selection_facts(env_file, windows_adapter=True)
    posix = selection_facts(env_file)
    assert windows["mode"] == "serve"
    assert windows["configured_enabled"] is True
    assert windows["label_opt_in"] is True
    assert posix["mode"] == "legacy"
    assert posix["configured_enabled"] is False


def test_native_state_vocabulary_preserves_transitions_but_not_arbitrary_text(tmp_path):
    from agent_dispatch.supervisor_lifecycle import _ACTIVE_STATES, _ENABLED_STATES
    invocation = Invocation("installer.sh", "update", "123-456", 123, 100)
    for enabled in sorted(_ENABLED_STATES):
        assert audit(tmp_path, "selection", "probe", invocation=invocation, native_enabled=enabled)
    for active in sorted(_ACTIVE_STATES):
        assert audit(tmp_path, "selection", "probe", invocation=invocation, native_active=active)
    assert audit(tmp_path, "selection", "probe", invocation=invocation, native_enabled="arbitrary private text")
    events = [json.loads(line)["detail"] for line in (tmp_path / "lifecycle.log").read_text().splitlines()]
    assert {e["native_enabled"] for e in events[:-1]} == _ENABLED_STATES
    assert {e["native_active"] for e in events[:-1]} == _ACTIVE_STATES
    assert events[-1]["native_enabled"] == "unknown"
