"""Ordinary embodiment observes native state without gaining launch custody."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import embody_receipt as receipt, handoff_cli, output


def _record(sid=None, pane="%1"):
    return SimpleNamespace(
        resolved_head_session=sid,
        session_entry=lambda _: SimpleNamespace(pane_id=pane),
    )


@pytest.fixture
def native(monkeypatch, tmp_path):
    state = SimpleNamespace(record=_record(), live="live", bindings=0)
    monkeypatch.setattr(receipt.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(receipt.tracking, "load_record", lambda _: state.record)
    monkeypatch.setattr(receipt.session_liveness, "session_liveness", lambda _: state.live)

    def binding(sid, **kwargs):
        assert kwargs["expected_session_name"] == "wt-example"
        state.bindings += 1
        return state.binding

    state.binding = {
        "worktree_id": "example", "session_name": "wt-example", "pane_id": "%1",
        "pane_pid": 100, "pane_start_time": "10",
        "copilot_pid": 101, "copilot_start_time": "11",
    }
    monkeypatch.setattr(receipt.sessions, "mux_binding_for_session", binding)
    return state


def _run(capfd, **options):
    timeout = options.pop("timeout", 0)
    result = {"ok": True, "created": True, "resumed": False,
              "worktree_id": "example", "session": "wt-example", "new_pane": "%1",
              "work_dir": "/example", "resume_session": None, **options}
    def operation(target):
        print("launch diagnostic")
        output._json_output(result)
        return 0
    rc = receipt.run(argparse.Namespace(wait_session_timeout=timeout), operation)
    streams = capfd.readouterr()
    assert streams.err == "launch diagnostic\n"
    return rc, json.loads(streams.out)


def test_fresh_unregistered_is_not_native_success(native, capfd, monkeypatch):
    monkeypatch.setattr(receipt.time, "sleep", lambda _: pytest.fail("default must not wait"))
    rc, result = _run(capfd)
    assert rc == 0 and result["ok"] is True
    assert result["created"] is True and result["resumed"] is False
    assert result["state"] == "starting" and result["session_id"] is None
    assert not result["session_verified"] and native.bindings == 0


@pytest.mark.parametrize("created,resume,action", [
    (True, None, "create"), (True, "original", "resume"), (False, None, "reuse"),
])
def test_native_confirmation_preserves_mux_semantics(native, capfd, created, resume, action):
    native.record = _record("original")
    rc, result = _run(capfd, created=created, resumed=not created, resume_session=resume)
    assert rc == 0 and result["state"] == "live"
    assert result["launch_action"] == action
    assert result["session_id"] == "original" and result["session_verified"]
    assert result["native_process"]["copilot_pid"] == 101
    assert native.bindings == 1
    # Legacy readers need no new receipt fields.
    assert result["version"] == 1 and result["created"] == created
    assert result["resumed"] == (not created) and result["session"] == "wt-example"


@pytest.mark.parametrize("head,pane,resume", [
    ("old", "%0", None), ("other", "%1", "selected"), ("old", None, None),
])
def test_fresh_result_never_accepts_an_incompatible_head(native, capfd, head, pane, resume):
    native.record = _record(head, pane)
    _, result = _run(capfd, resume_session=resume)
    assert result["session_id"] is None and result["state"] == "starting"
    assert result["session_reason"] == "head-not-launch" and native.bindings == 0


@pytest.mark.parametrize("live", ["dead", "unknown"])
def test_registered_head_is_not_live_without_evidence(native, capfd, live):
    native.record = _record("original")
    native.live = live
    _, result = _run(capfd, created=False)
    assert result["session_id"] == "original" and result["state"] == "unknown"
    assert result["session_reason"] == f"native-{live}" and not result["session_verified"]
    assert native.bindings == 0


def test_wait_observes_late_registration_without_relaunch(native, capfd, monkeypatch):
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
        native.record = _record("new")
    monkeypatch.setattr(receipt.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(receipt.time, "sleep", sleep)
    rc, result = _run(capfd, timeout=2)
    assert rc == 0 and result["session_id"] == "new"
    assert result["session_wait"] == "confirmed" and native.bindings == 1
    assert now[0] == 0.3


def test_wait_timeout_retains_target_and_possible_effects(native, capfd, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(receipt.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(receipt.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    rc, result = _run(capfd, timeout=0.5)
    assert rc == 3 and result["ok"] is False and result["session_wait"] == "timeout"
    assert result["created"] is True and result["worktree_id"] == "example"
    assert result["session"] == "wt-example" and result["new_pane"] == "%1"
    assert "do not create another process" in result["error"]
    assert now[0] == 0.5 and native.bindings == 0


def test_unconfirmed_binding_is_not_censused_every_poll(native, capfd, monkeypatch):
    native.record = _record("original")
    native.binding = None
    now = [0.0]
    monkeypatch.setattr(receipt.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(receipt.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    rc, result = _run(capfd, timeout=1)
    assert rc == 3 and native.bindings == 1
    assert result["session_reason"] == "mux-binding-unconfirmed"


def test_head_change_during_census_is_not_confirmation(native, capfd, monkeypatch):
    native.record = _record("old")
    def binding(*args, **kwargs):
        native.record = _record("new")
        return native.binding
    monkeypatch.setattr(receipt.sessions, "mux_binding_for_session", binding)
    _, result = _run(capfd, created=False)
    assert not result["session_verified"] and result["session_reason"] == "head-changed"


def test_wrong_mux_binding_is_not_native_confirmation(native, capfd):
    native.record = _record("original")
    native.binding["pane_id"] = "%2"
    _, result = _run(capfd)
    assert not result["session_verified"] and result["session_reason"] == "mux-binding-mismatch"


def test_head_replacement_cannot_reuse_a_previously_sampled_binding(native, monkeypatch):
    native.record = _record("original")
    def binding(*args, **kwargs):
        native.record = _record("replacement")
        return native.binding
    monkeypatch.setattr(receipt.sessions, "mux_binding_for_session", binding)
    result = {"worktree_id": "example", "session": "wt-example", "created": False}
    bindings = {}
    assert receipt._observe(result, bindings)["session_reason"] == "head-changed"
    native.record = _record("original")
    assert not receipt._observe(result, bindings)["session_verified"]


def test_launch_failure_retains_resolved_target_without_claiming_no_effects(native, capfd):
    def operation(target):
        target.update(worktree_id="example", work_dir="/example",
                      session="wt-example", launch_attempted=True)
        return output._json_error("creation could not be confirmed", 4)
    rc = receipt.run(argparse.Namespace(wait_session_timeout=1), operation)
    result = json.loads(capfd.readouterr().out)
    assert rc == 4 and result["launch_action"] == "attempted"
    assert result["state"] == "launch-failed" and result["session_id"] is None
    assert result["worktree_id"] == "example" and result["session"] == "wt-example"
    assert native.bindings == 0


def test_post_launch_error_preserves_launch_and_native_observation(native, capfd):
    native.record = _record("original")
    def operation(target):
        target.update(worktree_id="example", session="wt-example",
                      created=True, new_pane="%1", launch_attempted=True)
        return output._json_error("startup receipt unavailable", 3)
    rc = receipt.run(argparse.Namespace(wait_session_timeout=1), operation)
    result = json.loads(capfd.readouterr().out)
    assert rc == 3 and result["error"] == "startup receipt unavailable"
    assert result["created"] is True and result["launch_action"] == "create"
    assert result["session_verified"] and result["state"] == "live"
    assert result["session_wait"] == "not-requested"


def test_dry_run_does_not_observe_or_wait(native, capfd, monkeypatch):
    monkeypatch.setattr(receipt.tracking, "load_record", lambda _: pytest.fail("no observation"))
    rc, result = _run(capfd, dry_run=True, timeout=2)
    assert rc == 0 and result["state"] == "dry-run"
    assert result["session_wait"] == "not-requested"


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "invalid"])
def test_parser_rejects_invalid_wait_before_launch(value):
    parser = argparse.ArgumentParser()
    handoff_cli.add_parsers(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["embody", "--new", f"--wait-session-timeout={value}"])
    assert exc.value.code == 2


def test_real_entrypoint_emits_one_receipt_and_separate_diagnostics(native, capfd, monkeypatch):
    monkeypatch.setattr(main.front_door_cli, "resolve_invocation_project", lambda *a: True)
    monkeypatch.setattr(main, "_guard_project_scope", lambda *a: None)
    def operation(args, target):
        target["worktree_id"] = args.worktree_id
        print("preflight diagnostic")
        output._json_output({"ok": True, "dry_run": True, "session": "wt-example"})
        return 0
    monkeypatch.setattr(handoff_cli, "_cmd_embody", operation)
    assert main.main(["embody", "--worktree-id", "example", "--json",
                      "--wait-session-timeout", "0"]) == 0
    streams = capfd.readouterr()
    result = json.loads(streams.out)
    assert result["worktree_id"] == "example" and result["state"] == "dry-run"
    assert streams.err == "preflight diagnostic\n"


@pytest.mark.parametrize("timeout", [0, 1])
def test_wait_is_rejected_for_native_restart(monkeypatch, capfd, timeout):
    monkeypatch.setattr(handoff_cli.native_restart_launch, "is_restart_intent", lambda _: True)
    monkeypatch.setattr(handoff_cli.native_restart_launch, "cmd_embody",
                        lambda _: pytest.fail("no restart effects"))
    assert handoff_cli.cmd_embody(argparse.Namespace(wait_session_timeout=timeout)) == 2
    assert "cannot be combined" in json.loads(capfd.readouterr().out)["error"]


def test_supported_legacy_parser_subset_keeps_baseline_and_refuses_optional_wait():
    # Frozen prior no-wait syntax, independent of the new provider parser.
    parser = argparse.ArgumentParser(allow_abbrev=False)
    embody = parser.add_subparsers(dest="command").add_parser("embody", allow_abbrev=False)
    embody.add_argument("--worktree-id")
    embody.add_argument("--resume-head", action="store_true")
    embody.add_argument("--json", action="store_true")
    baseline = ["embody", "--worktree-id", "example", "--resume-head", "--json"]
    assert parser.parse_args(baseline).worktree_id == "example"
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([*baseline, "--wait-session-timeout", "1"])
    assert exc.value.code == 2
