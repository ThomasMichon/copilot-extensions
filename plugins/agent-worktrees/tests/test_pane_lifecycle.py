"""Tests for isolated pane create/terminate primitives and their CLI harness."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import pane_lifecycle, sessions, sessions_pane_retire


class _RunResult:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_pane_create_logs_before_spawn_and_foregrounds(monkeypatch, tmp_path: Path):
    receipt = tmp_path / "receipt"
    call_order: list[str] = []
    logged: list[str] = []

    monkeypatch.setattr(sessions, "has_mux_session", lambda worktree_id: True)
    monkeypatch.setattr(
        sessions,
        "build_mux_new_window_argv",
        lambda *args, **kwargs: ["tmux", "new-window"],
    )
    monkeypatch.setattr(
        sessions,
        "_initial_prompt_receipt_path",
        lambda token: receipt,
    )
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions, "_mux_pane_alive", lambda pane, mux_bin, session_name=None: True
    )
    monkeypatch.setattr(
        sessions,
        "mux_focus_pane",
        lambda session_name, pane_id, **kwargs: (
            call_order.append("focus") or True
        ),
    )

    def _log_event(event: str, **kwargs) -> None:
        call_order.append(f"log:{event}")
        logged.append(event)

    def _run(argv, **kwargs):
        call_order.append("run")
        receipt.write_text("launching", encoding="utf-8")
        return _RunResult(stdout="%7\n")

    monkeypatch.setattr(pane_lifecycle.activity, "log_event", _log_event)
    monkeypatch.setattr(pane_lifecycle.subprocess, "run", _run)

    result = pane_lifecycle.pane_create(
        "abc",
        "/w/abc",
        ["copilot"],
        mux="tmux",
        prompt_receipt_timeout=1.0,
        prompt_startup_grace=0.0,
    )

    assert result["ok"] is True
    assert result["new_pane"] == "%7"
    assert result["pane_id"] == "%7"
    assert result["foregrounded"] is True
    assert logged[:2] == ["pane_create_started", "mux_session_assigned"]
    assert call_order[:3] == ["log:pane_create_started", "run", "log:mux_session_assigned"]


def test_pane_create_uses_new_session_when_no_worktree_mux_exists(
    monkeypatch, tmp_path: Path
):
    receipt = tmp_path / "receipt"

    monkeypatch.setattr(sessions, "has_mux_session", lambda worktree_id: False)
    monkeypatch.setattr(
        sessions,
        "build_mux_new_session_argv",
        lambda *args, **kwargs: ["tmux", "new-session"],
    )
    monkeypatch.setattr(
        sessions,
        "_initial_prompt_receipt_path",
        lambda token: receipt,
    )
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions, "_mux_pane_alive", lambda pane, mux_bin, session_name=None: True
    )
    monkeypatch.setattr(sessions, "mux_focus_pane", lambda *args, **kwargs: True)

    def _run(argv, **kwargs):
        receipt.write_text("launching", encoding="utf-8")
        return _RunResult(stdout="%11\n")

    monkeypatch.setattr(pane_lifecycle.subprocess, "run", _run)
    monkeypatch.setattr(pane_lifecycle.activity, "log_event", lambda *args, **kwargs: None)

    result = pane_lifecycle.pane_create(
        "def",
        "/w/def",
        ["copilot"],
        mux="tmux",
        prompt_receipt_timeout=1.0,
        prompt_startup_grace=0.0,
    )

    assert result["ok"] is True
    assert result["method"] == "new-session"
    assert result["mux_session"] == "wt-def"


def test_pane_create_retires_failed_successor_when_receipt_never_arrives(
    monkeypatch, tmp_path: Path
):
    receipt = tmp_path / "receipt"
    cleaned: dict[str, object] = {}

    monkeypatch.setattr(sessions, "has_mux_session", lambda worktree_id: True)
    monkeypatch.setattr(
        sessions,
        "build_mux_new_window_argv",
        lambda *args, **kwargs: ["tmux", "new-window"],
    )
    monkeypatch.setattr(
        sessions,
        "_initial_prompt_receipt_path",
        lambda token: receipt,
    )
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {101, 202})
    monkeypatch.setattr(
        sessions,
        "_retire_failed_successor",
        lambda pane_id, tree, **kwargs: cleaned.update(pane=pane_id, tree=tree) or {"ok": True},
    )
    monkeypatch.setattr(pane_lifecycle.activity, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pane_lifecycle.subprocess,
        "run",
        lambda argv, **kwargs: _RunResult(stdout="%9\n"),
    )

    result = pane_lifecycle.pane_create(
        "ghi",
        "/w/ghi",
        ["copilot"],
        mux="tmux",
        prompt_receipt_timeout=0.0,
        prompt_startup_grace=0.0,
    )

    assert result["ok"] is False
    assert result["prompt_received"] is False
    assert cleaned == {"pane": "%9", "tree": {101, 202}}


def test_pane_create_retires_successor_when_foreground_fails(
    monkeypatch, tmp_path: Path,
):
    # The launch was confirmed (prompt_received=True), but mux_focus_pane
    # could not make it the operator's current pane -- the same "invisible
    # orphan" failure mode as a receipt never arriving, previously left
    # uncleaned.
    receipt = tmp_path / "receipt"
    cleaned: dict[str, object] = {}

    monkeypatch.setattr(sessions, "has_mux_session", lambda worktree_id: True)
    monkeypatch.setattr(
        sessions,
        "build_mux_new_window_argv",
        lambda *args, **kwargs: ["tmux", "new-window"],
    )
    monkeypatch.setattr(
        sessions,
        "_initial_prompt_receipt_path",
        lambda token: receipt,
    )
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions, "_mux_pane_alive", lambda pane, mux_bin, session_name=None: True
    )
    monkeypatch.setattr(sessions, "mux_focus_pane", lambda *args, **kwargs: False)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {303, 404})
    monkeypatch.setattr(
        sessions,
        "_retire_failed_successor",
        lambda pane_id, tree, **kwargs: cleaned.update(pane=pane_id, tree=tree) or {"ok": True},
    )
    monkeypatch.setattr(pane_lifecycle.activity, "log_event", lambda *args, **kwargs: None)

    def _run(argv, **kwargs):
        receipt.write_text("launching", encoding="utf-8")
        return _RunResult(stdout="%13\n")

    monkeypatch.setattr(pane_lifecycle.subprocess, "run", _run)

    result = pane_lifecycle.pane_create(
        "jkl",
        "/w/jkl",
        ["copilot"],
        mux="tmux",
        prompt_receipt_timeout=1.0,
        prompt_startup_grace=0.0,
    )

    assert result["ok"] is False
    assert result["foregrounded"] is False
    assert result["cleanup"] == {"ok": True}
    assert cleaned == {"pane": "%13", "tree": {303, 404}}


def test_pane_terminate_graceful_via_liveness_only(monkeypatch):
    state = {"send_count": 0}

    def _run(argv, **kwargs):
        if argv[1] == "send-keys":
            state["send_count"] += 1
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions_pane_retire,
        "_mux_qualified_pane_target",
        lambda pane_id, mux_bin, session_name=None: "wt-demo:0.0",
    )
    monkeypatch.setattr(
        sessions,
        "_mux_pane_alive",
        lambda pane_id, mux_bin, session_name=None: state["send_count"] < 2,
    )
    monkeypatch.setattr(sessions, "_mux_last_window_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {10, 11})
    monkeypatch.setattr(
        pane_lifecycle.subprocess,
        "run",
        _run,
    )
    monkeypatch.setattr(pane_lifecycle.time, "sleep", lambda seconds: None)

    result = pane_lifecycle.pane_terminate(
        "%3",
        mux="tmux",
        mux_session="wt-demo",
        overall_budget=2.0,
        hard_kill_settle=0.1,
    )

    assert result["ok"] is True
    assert result["gone"] is True
    assert result["method"] == "graceful"
    assert state["send_count"] == 2


def test_pane_terminate_reports_signature_confirmed_graceful_exit(monkeypatch):
    alive_states = iter([True, True, True, True, False])
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2, 0.5])

    def _alive(pane_id, mux_bin):
        return next(alive_states)

    def _mono():
        return next(monotonic_values)

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions_pane_retire,
        "_mux_qualified_pane_target",
        lambda pane_id, mux_bin, session_name=None: "wt-demo:0.0",
    )
    monkeypatch.setattr(
        sessions,
        "_mux_pane_alive",
        lambda pane_id, mux_bin, session_name=None: _alive(pane_id, mux_bin),
    )
    monkeypatch.setattr(sessions, "_mux_last_window_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {10})
    monkeypatch.setattr(
        pane_lifecycle.subprocess,
        "run",
        lambda argv, **kwargs: _RunResult(
            stdout="Goodbye from Copilot\n" if argv[1] == "capture-pane" else ""
        ),
    )
    monkeypatch.setattr(pane_lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pane_lifecycle.time, "monotonic", _mono)

    result = pane_lifecycle.pane_terminate(
        "%4",
        mux="tmux",
        mux_session="wt-demo",
        overall_budget=2.0,
        escalate_after=0.3,
        hard_kill_settle=0.1,
    )

    assert result["ok"] is True
    assert result["gone"] is True
    assert result["method"] == "graceful-signature-confirmed"
    assert result["signature_seen"] is True


def test_pane_terminate_escalates_to_hard_kill_and_cleans_locks(monkeypatch):
    state = {"killed": False}
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2, 0.4, 0.5, 0.8, 1.0, 1.1, 1.2])

    def _alive(pane_id, mux_bin):
        return not state["killed"]

    def _run(argv, **kwargs):
        if argv[1] == "kill-pane":
            state["killed"] = True
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions_pane_retire,
        "_mux_qualified_pane_target",
        lambda pane_id, mux_bin, session_name=None: "wt-demo:0.0",
    )
    monkeypatch.setattr(
        sessions,
        "_mux_pane_alive",
        lambda pane_id, mux_bin, session_name=None: _alive(pane_id, mux_bin),
    )
    monkeypatch.setattr(sessions, "_mux_last_window_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {33, 44})
    monkeypatch.setattr(
        pane_lifecycle,
        "_cleanup_pane_lock_residue",
        lambda pane_id, pane_session, process_tree: [{"pid": 44, "path": "lock"}],
    )
    monkeypatch.setattr(pane_lifecycle.subprocess, "run", _run)
    monkeypatch.setattr(pane_lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pane_lifecycle.time, "monotonic", lambda: next(monotonic_values))

    result = pane_lifecycle.pane_terminate(
        "%5",
        mux="tmux",
        mux_session="wt-demo",
        overall_budget=0.6,
        poll_interval=0.0,
        ctrl_c_gap=0.0,
        escalate_after=0.1,
        hard_kill_settle=0.1,
    )

    assert result["ok"] is True
    assert result["method"] == "hard"
    assert result["locks_cleared"] == [{"pid": 44, "path": "lock"}]
    assert state["killed"] is True


def test_pane_terminate_uses_session_qualified_targets_when_mux_session_known(monkeypatch):
    state = {"killed": False, "targets": [], "list_calls": 0}
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2, 0.4, 0.5, 0.8, 1.0, 1.1, 1.2])

    def _run(argv, **kwargs):
        command = argv[1]
        if command == "list-panes":
            assert argv[2] == "-a"
            state["list_calls"] += 1
            if state["killed"]:
                return _RunResult(stdout="")
            return _RunResult(stdout="wt-demo\t0.0\t%5\nwt-other\t0.0\t%1\n")
        if command in {"send-keys", "capture-pane", "kill-pane"}:
            state["targets"].append((command, argv[argv.index("-t") + 1]))
            if command == "kill-pane":
                state["killed"] = True
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(sessions, "_mux_last_window_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {33})
    monkeypatch.setattr(
        pane_lifecycle,
        "_cleanup_pane_lock_residue",
        lambda pane_id, pane_session, process_tree: [],
    )
    monkeypatch.setattr(pane_lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pane_lifecycle.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("subprocess.run", _run)

    result = pane_lifecycle.pane_terminate(
        "%5",
        mux="psmux",
        mux_session="wt-demo",
        overall_budget=0.6,
        poll_interval=0.0,
        ctrl_c_gap=0.0,
        escalate_after=0.1,
        hard_kill_settle=0.1,
    )

    assert result["ok"] is True
    assert result["method"] == "hard"
    assert all(target == "wt-demo:0.0" for _command, target in state["targets"])
    assert [command for command, _target in state["targets"]].count("send-keys") == 3
    assert [command for command, _target in state["targets"]].count("kill-pane") == 1
    assert [command for command, _target in state["targets"]].count("capture-pane") >= 3
    assert state["list_calls"]


def test_pane_terminate_uses_qualified_target_for_pane_in_non_active_window(monkeypatch):
    """Regression for issue #2892: a session-scoped ``list-panes`` query must
    see every window in the session, not just its currently-active one --
    otherwise a known-correct ``mux_session`` for a predecessor pane sitting
    in an older, non-focused window falls through to the global ambiguity
    scan and can falsely refuse as ``ambiguous-pane-id``."""
    state = {"killed": False, "targets": []}
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2, 0.4, 0.5, 0.8, 1.0, 1.1, 1.2])

    def _run(argv, **kwargs):
        command = argv[1]
        if command == "list-panes":
            assert argv[2] == "-a"
            if state["killed"]:
                return _RunResult(stdout="")
            # Two windows in the same session: %1 (window 0, NOT the active
            # one) is the predecessor pane being retired; %3 (window 1) is
            # the successor pane. A same-scoped ``-t <session>`` query would
            # only see the active window (%3) and miss %1 entirely.
            return _RunResult(
                stdout="wt-cutover\t0.0\t%1\nwt-cutover\t1.0\t%3\n"
            )
        if command in {"send-keys", "capture-pane", "kill-pane"}:
            state["targets"].append((command, argv[argv.index("-t") + 1]))
            if command == "kill-pane":
                state["killed"] = True
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(sessions, "_mux_last_window_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {33})
    monkeypatch.setattr(
        pane_lifecycle,
        "_cleanup_pane_lock_residue",
        lambda pane_id, pane_session, process_tree: [],
    )
    monkeypatch.setattr(pane_lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pane_lifecycle.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("subprocess.run", _run)

    result = pane_lifecycle.pane_terminate(
        "%1",
        mux="psmux",
        mux_session="wt-cutover",
        overall_budget=0.6,
        poll_interval=0.0,
        ctrl_c_gap=0.0,
        escalate_after=0.1,
        hard_kill_settle=0.1,
    )

    assert result["method"] != "ambiguous-pane-id"
    assert result["ok"] is True
    assert result["method"] == "hard"
    assert all(target == "wt-cutover:0.0" for _command, target in state["targets"])


def test_pane_terminate_resolves_unambiguous_session_before_signaling(monkeypatch):
    state = {"killed": False, "send_target": None, "global_scans": 0}
    monotonic_values = iter([0.0, 0.0, 0.1, 0.2, 0.4, 0.5, 0.8, 1.0, 1.1, 1.2])

    def _run(argv, **kwargs):
        command = argv[1]
        if command == "list-panes":
            fmt = argv[-1]
            if "-a" in argv:
                state["global_scans"] += 1
                if state["killed"]:
                    return _RunResult(stdout="")
                assert fmt == "#{session_name}\t#{window_index}.#{pane_index}\t#{pane_id}"
                return _RunResult(stdout="wt-one\t0.0\t%7\n")
            target = argv[argv.index("-t") + 1]
            if state["killed"]:
                return _RunResult(stdout="")
            assert target == "wt-one"
            return _RunResult(stdout="wt-one\t0.0\t%7\n")
        if command == "send-keys":
            state["send_target"] = argv[argv.index("-t") + 1]
        if command == "kill-pane":
            state["killed"] = True
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(sessions, "_mux_last_window_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(sessions, "_mux_pane_process_tree", lambda *args, **kwargs: {44})
    monkeypatch.setattr(
        pane_lifecycle,
        "_cleanup_pane_lock_residue",
        lambda pane_id, pane_session, process_tree: [],
    )
    monkeypatch.setattr(pane_lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pane_lifecycle.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("subprocess.run", _run)

    result = pane_lifecycle.pane_terminate(
        "%7",
        mux="psmux",
        overall_budget=0.6,
        poll_interval=0.0,
        ctrl_c_gap=0.0,
        escalate_after=0.1,
        hard_kill_settle=0.1,
    )

    assert result["ok"] is True
    assert result["session"] == "wt-one"
    assert result["method"] == "hard"
    assert state["global_scans"] >= 1
    assert state["send_target"] == "wt-one:0.0"


def test_pane_terminate_refuses_ambiguous_bare_pane_id(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "list-panes" and "-a" in argv:
            return _RunResult(stdout="wt-a\t0.0\t%1\nwt-b\t0.0\t%1\n")
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr("subprocess.run", _run)

    result = pane_lifecycle.pane_terminate("%1", mux="psmux")

    assert result == {
        "ok": False,
        "pane": "%1",
        "gone": False,
        "method": "ambiguous-pane-id",
        "session": None,
        "signature_seen": False,
        "signature_pattern": None,
        "locks_cleared": [],
        "candidate_sessions": ["wt-a", "wt-b"],
        "error": "pane id %1 is ambiguous across mux sessions: wt-a, wt-b",
    }
    assert len(calls) == 1
    assert calls[0][:3] == ["psmux", "list-panes", "-a"]


def test_pane_terminate_skips_last_window_and_logs_guard(monkeypatch):
    logged: list[str] = []

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions_pane_retire,
        "_mux_qualified_pane_target",
        lambda pane_id, mux_bin, session_name=None: "wt-guard:0.0",
    )
    monkeypatch.setattr(
        sessions, "_mux_pane_alive", lambda pane_id, mux_bin, session_name=None: True
    )
    monkeypatch.setattr(
        sessions,
        "_mux_last_window_guard",
        lambda pane_id, mux_bin, session_name=None: {
            "session": "wt-guard",
            "window_count": 1,
        },
    )
    monkeypatch.setattr(
        pane_lifecycle.activity,
        "log_event",
        lambda event, **kwargs: logged.append(event),
    )

    result = pane_lifecycle.pane_terminate("%6", mux="tmux", mux_session="wt-guard")

    assert result["ok"] is True
    assert result["gone"] is False
    assert result["method"] == "last-window-skip"
    assert logged == ["handoff_retire_guard"]


def test_cmd_pane_create_invokes_primitive_and_outputs_json(monkeypatch):
    observed: dict[str, object] = {}
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        pane_lifecycle,
        "pane_create",
        lambda *args, **kwargs: observed.update(
            worktree_id=args[0], work_dir=args[1], cmd=args[2], env=args[3], kwargs=kwargs
        ) or {"ok": True, "pane_id": "%8"},
    )
    monkeypatch.setattr(
        pane_lifecycle.output,
        "_json_output",
        lambda payload: emitted.append(payload),
    )

    rc = pane_lifecycle.cmd_pane_create(
        argparse.Namespace(
            worktree_id="wt-cli",
            work_dir="/w/cli",
            cmd=["--", "copilot", "--allow-all-tools"],
            env=["A=B"],
            mux="tmux",
            session_name=None,
            payload_receipt_token=None,
            initial_prompt=None,
            receipt_timeout=8.0,
            startup_grace=3.5,
            json=True,
        )
    )

    assert rc == 0
    assert observed["cmd"] == ["copilot", "--allow-all-tools"]
    assert observed["env"] == {"A": "B"}
    assert emitted == [{"ok": True, "pane_id": "%8"}]


def test_cmd_pane_terminate_invokes_primitive_and_outputs_json(monkeypatch):
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        pane_lifecycle,
        "pane_terminate",
        lambda *args, **kwargs: {"ok": True, "pane": args[0], "method": "graceful"},
    )
    monkeypatch.setattr(
        pane_lifecycle.output,
        "_json_output",
        lambda payload: emitted.append(payload),
    )

    rc = pane_lifecycle.cmd_pane_terminate(
        argparse.Namespace(
            pane_id="%22",
            mux="tmux",
            mux_session="wt-two",
            overall_budget=30.0,
            poll_interval=0.3,
            ctrl_c_gap=0.6,
            escalate_after=1.5,
            hard_kill_settle=1.5,
            exit_pattern=None,
            json=True,
        )
    )

    assert rc == 0
    assert emitted == [{"ok": True, "pane": "%22", "method": "graceful"}]
