"""Tests for isolated pane create/terminate primitives and their CLI harness."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import pane_lifecycle, sessions


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
    monkeypatch.setattr(sessions, "_mux_pane_alive", lambda pane, mux_bin: True)
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
    monkeypatch.setattr(sessions, "_mux_pane_alive", lambda pane, mux_bin: True)
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


def test_pane_terminate_graceful_via_liveness_only(monkeypatch):
    state = {"send_count": 0}

    def _run(argv, **kwargs):
        if argv[1] == "send-keys":
            state["send_count"] += 1
        return _RunResult()

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(
        sessions,
        "_mux_pane_alive",
        lambda pane_id, mux_bin: state["send_count"] < 2,
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
    monkeypatch.setattr(sessions, "_mux_pane_alive", _alive)
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
    monkeypatch.setattr(sessions, "_mux_pane_alive", _alive)
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


def test_pane_terminate_skips_last_window_and_logs_guard(monkeypatch):
    logged: list[str] = []

    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "tmux")
    monkeypatch.setattr(sessions, "_mux_pane_alive", lambda pane_id, mux_bin: True)
    monkeypatch.setattr(
        sessions,
        "_mux_last_window_guard",
        lambda pane_id, mux_bin: {"session": "wt-guard", "window_count": 1},
    )
    monkeypatch.setattr(
        pane_lifecycle.activity,
        "log_event",
        lambda event, **kwargs: logged.append(event),
    )

    result = pane_lifecycle.pane_terminate("%6", mux="tmux")

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
