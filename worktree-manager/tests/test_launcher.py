"""Tests for the launch executor + mux-by-capability composition (slice 3, DQ9).

These pin the DQ9 rule: the Manager owns mux, so a launch is muxed **only** when a
Manager-owned mux capability is present and the caller wants it -- never gated on
the plan's ``no_mux`` (which the engine always sets in ``--json`` mode). The pure
:func:`compose_launch` is exercised with a fake capability so nothing spawns a real
Copilot; :func:`execute` runs a harmless argv.
"""

from __future__ import annotations

import sys

import pytest

from worktree_manager import launcher
from worktree_manager.engine_client import LaunchPlan


@pytest.fixture(autouse=True)
def _reset_capability(monkeypatch):
    launcher.set_mux_capability(None)
    monkeypatch.delenv(launcher.MUX_ENV, raising=False)
    yield
    launcher.set_mux_capability(None)


def _exec_plan(**over) -> LaunchPlan:
    base = dict(action="exec", cmd=["copilot", "--resume=abc"], work_dir="/w/x",
                status_path="/w/x", env={"FOO": "bar"},
                worktree_id="m-win-1200-ab12", post_exit=True, no_mux=True,
                exit_code=0, raw={})
    base.update(over)
    return LaunchPlan(**base)


def _fake_mux() -> launcher.MuxCapability:
    def wrap(argv, session, work_dir):
        return ["mux", "new", "-s", session, "--", *argv]
    return launcher.MuxCapability(name="fake", available=True, wrap=wrap)


def test_compose_direct_when_no_capability():
    le = launcher.compose_launch(_exec_plan())
    assert le.kind == "exec"
    assert le.muxed is False
    assert le.argv == ["copilot", "--resume=abc"]
    assert le.cwd == "/w/x"
    assert le.env["FOO"] == "bar"
    # The plan's env is merged over the ambient process env.
    assert "PATH" in le.env or "Path" in le.env


def test_compose_muxes_ignoring_plan_no_mux():
    # plan.no_mux is True (as --json always sets); the Manager still muxes because
    # it owns mux and the caller wants it -- DQ9's key inversion.
    le = launcher.compose_launch(_exec_plan(no_mux=True), _fake_mux())
    assert le.muxed is True
    assert le.argv[:4] == ["mux", "new", "-s", "wt-m-win-1200-ab12"]
    assert le.argv[-2:] == ["copilot", "--resume=abc"]


def test_want_mux_false_forces_direct():
    le = launcher.compose_launch(_exec_plan(), _fake_mux(), want_mux=False)
    assert le.muxed is False
    assert le.argv == ["copilot", "--resume=abc"]


def test_env_off_forces_direct(monkeypatch):
    monkeypatch.setenv(launcher.MUX_ENV, "off")
    launcher.set_mux_capability(_fake_mux())
    le = launcher.compose_launch(_exec_plan())
    assert le.muxed is False


def test_active_capability_is_used_by_default():
    launcher.set_mux_capability(_fake_mux())
    le = launcher.compose_launch(_exec_plan())
    assert le.muxed is True


def test_none_action_composes_to_noop_with_exit_code():
    plan = _exec_plan(action="none", cmd=[], exit_code=7)
    le = launcher.compose_launch(plan)
    assert le.kind == "none"
    assert le.argv == []
    assert launcher.execute(le) == 7


def test_execute_runs_argv_and_returns_code():
    plan = _exec_plan(cmd=[sys.executable, "-c", "import sys; sys.exit(3)"],
                      work_dir=None, no_mux=True, env={})
    assert launcher.launch(plan) == 3


def test_execute_success_path():
    plan = _exec_plan(cmd=[sys.executable, "-c", "print('ok')"],
                      work_dir=None, env={})
    assert launcher.launch(plan) == 0
