"""Unit tests for the postToolUse disposition-nudge hook (scripts/nudge_status.py).

The hook is a standalone script (runs under system python, no agent_worktrees
import), so we load it by path and exercise the pure ``decide()`` seam against a
fabricated ``home`` (tracking yaml + sidecar), never the real ~/.agent-worktrees.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nudge_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("nudge_status", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nudge = _load()

_WID = "examplehost-20260101-000000-abcd"


@pytest.mark.parametrize("error,expected", [(5, False), (87, True), (6, False)])
def test_worker_probe_does_not_mistake_access_denial_for_exit(monkeypatch, error, expected):
    import ctypes
    from types import SimpleNamespace

    class Api:
        def __call__(self, *args):
            return 0

    kernel = SimpleNamespace(OpenProcess=Api(), WaitForSingleObject=Api(), CloseHandle=Api())
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(nudge, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(nudge, "sys", SimpleNamespace(modules={}))
    assert nudge._worker_definitely_gone({"pid": 123, "start_time": "fixture"}) is expected


def test_worker_probe_retains_a_genuinely_live_owner():
    import os
    from agent_worktrees import local_cache_worker

    assert not nudge._worker_definitely_gone(local_cache_worker._owner(os.getpid()))


def _make_home(tmp_path: Path, *, project="dotfiles", state="active",
               note_at="2026-01-01T00:00:00+00:00") -> tuple[Path, str]:
    """Create a fake home with one tracked worktree; return (home, cwd)."""
    tdir = tmp_path / f".{project}" / "worktrees"
    tdir.mkdir(parents=True)
    body = f"id: {_WID}\nstate: {state}\n"
    if note_at is not None:
        body += f"status_note_at: '{note_at}'\n"
    (tdir / f"{_WID}.yaml").write_text(body, encoding="utf-8")
    # A cwd whose ancestor basename is the worktree id (need not exist on disk).
    cwd = str(tmp_path / "src" / ".worktrees" / project / _WID / "sub")
    return tmp_path, cwd


def _payload(cwd):
    return {"toolName": "view", "cwd": cwd}


def test_no_nudge_before_threshold(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "5", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    for i in range(4):
        assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + i) is None


def test_nudge_at_call_threshold_then_cooldown(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "3", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    outs = [nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + i)
            for i in range(6)]
    fired = [i for i, o in enumerate(outs) if o]
    # Fires on the 3rd call (index 2), resets, needs another full window -> 6th (index 5).
    assert fired == [2, 5]
    assert "status --activity" in outs[2]
    assert "--title" in outs[2]  # summary_and_title scope


def test_time_threshold_triggers(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "999", "AGENT_WORKTREES_NUDGE_MINUTES": "20"}
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0) is None
    # 21 minutes later -> due on elapsed time even though call count is tiny.
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + 21 * 60) is not None


def test_reset_when_disposition_written(tmp_path):
    home, cwd = _make_home(tmp_path, note_at="2026-01-01T00:00:00+00:00")
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "3", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    nudge.decide(_payload(cwd), env=env, home=home, now=1000.0)
    nudge.decide(_payload(cwd), env=env, home=home, now=1001.0)  # count now 2
    # Agent writes a disposition -> status_note_at advances.
    yml = home / ".dotfiles" / "worktrees" / f"{_WID}.yaml"
    yml.write_text(
        f"id: {_WID}\nstate: active\nstatus_note_at: '2026-01-01T00:05:00+00:00'\n",
        encoding="utf-8")
    # Next fire resets the window (count back to 1), so no nudge yet at count 1/2.
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1002.0) is None
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1003.0) is None
    # Third post-reset call hits the threshold again.
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1004.0) is not None


def test_terminal_state_clears_sidecar_and_is_silent(tmp_path):
    home, cwd = _make_home(tmp_path, state="active")
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0) is not None
    sidecar = home / ".agent-worktrees" / "nudge-state" / f"{_WID}.json"
    assert sidecar.is_file()
    # Worktree finalized -> terminal state: no nudge, sidecar dropped.
    yml = home / ".dotfiles" / "worktrees" / f"{_WID}.yaml"
    yml.write_text(f"id: {_WID}\nstate: finalized\n", encoding="utf-8")
    assert nudge.decide(_payload(cwd), env=env, home=home, now=1001.0) is None
    assert not sidecar.exists()


def test_untracked_cwd_is_silent(tmp_path):
    home, _ = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1"}
    outside = str(tmp_path / "not" / "a" / "worktree")
    assert nudge.decide({"cwd": outside}, env=env, home=home, now=1000.0) is None


def test_kill_switch(tmp_path):
    home, cwd = _make_home(tmp_path)
    for off in ("off", "0", "false", "no"):
        env = {"AGENT_WORKTREES_NUDGE": off, "AGENT_WORKTREES_NUDGE_CALLS": "1"}
        assert nudge.decide(_payload(cwd), env=env, home=home, now=1000.0) is None


def test_expired_deadline_does_not_commit_sidecar(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1"}
    assert nudge.decide(
        _payload(cwd), env=env, home=home, deadline=0
    ) is None
    sidecar = home / ".agent-worktrees" / "nudge-state" / f"{_WID}.json"
    assert not sidecar.exists()


@pytest.mark.parametrize("scope_key", ["summary", "title"])
def test_nudge_mentions_both_summary_and_title(tmp_path, scope_key):
    home, cwd = _make_home(tmp_path)
    env = {"AGENT_WORKTREES_NUDGE_CALLS": "1", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}
    text = nudge.decide(_payload(cwd), env=env, home=home, now=1000.0)
    assert text is not None
    assert f"--{scope_key}" in text


_AGENDA_ENV = {"AGENT_WORKTREES_NUDGE_CALLS": "3", "AGENT_WORKTREES_NUDGE_MINUTES": "999"}


def _publish_policy(home, policy, *, version=1):
    import json

    path = home / ".agent-worktrees" / "nudge-state" / f"{_WID}{nudge.AGENDA_POLICY_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "policy": policy, "written_at": 1}), "utf-8")
    return path


def _agenda_runs(home, cwd, policy, calls=6, env=_AGENDA_ENV):
    if policy is not None:
        _publish_policy(home, policy)
    return [
        nudge.decide(_payload(cwd), env=env, home=home, now=1000.0 + i)
        for i in range(calls)
    ]


def _has_agenda(out):
    return bool(out) and "driving-effort-agendas" in out


def test_agenda_nudge_silent_when_policy_off(tmp_path):
    home, cwd = _make_home(tmp_path)
    assert not any(_has_agenda(o) for o in _agenda_runs(home, cwd, "off"))


def test_agenda_nudge_throttled_when_expected_and_lane_missing(tmp_path):
    home, cwd = _make_home(tmp_path)
    outs = _agenda_runs(home, cwd, "expected")
    assert [i for i, o in enumerate(outs) if _has_agenda(o)] == [2, 5]
    assert "agendas.default: expected" in outs[2]


def test_agenda_nudge_reads_policy_published_under_agent_home(tmp_path):
    agent_home, cwd = _make_home(tmp_path)
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    _publish_policy(agent_home, "expected")
    env = {**_AGENDA_ENV, "AGENT_HOME": str(agent_home)}

    outs = [nudge.decide(_payload(cwd), env=env, home=real_home, now=1000.0 + i) for i in range(3)]

    assert _has_agenda(outs[2])
    assert not (real_home / ".agent-worktrees").exists()


def test_forwarded_env_replaces_resident_switches_with_session_values():
    base = {"AGENT_HOME": "/sandbox", "AGENT_WORKTREES_NUDGE": "off"}
    payload = {"_agentWorktrees": {"environment": {"AGENT_WORKTREES_AGENDA_NUDGE": "off", "PATH": "x"}}}

    env = nudge.forwarded_env(payload, base)

    assert env == {"AGENT_HOME": "/sandbox", "AGENT_WORKTREES_AGENDA_NUDGE": "off"}
    assert nudge.forwarded_env({}, base) is None


def test_session_agenda_opt_out_reaches_resident_decision(tmp_path):
    home, cwd = _make_home(tmp_path)
    _publish_policy(home, "expected")
    payload = {**_payload(cwd), "_agentWorktrees": {"environment": {"AGENT_WORKTREES_AGENDA_NUDGE": "off"}}}
    env = nudge.forwarded_env(payload, dict(_AGENDA_ENV))

    outs = [nudge.decide(payload, env=env, home=home, now=1000.0 + i) for i in range(6)]

    assert not any(_has_agenda(o) for o in outs)


def test_hook_client_forwards_exactly_the_nudge_switches():
    spec = importlib.util.spec_from_file_location("hook_client_keys", _SCRIPT.with_name("hook_client.py"))
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)

    assert client._POST_TOOL_ENV_KEYS == nudge.NUDGE_ENV_KEYS
    resident_ps = _SCRIPT.with_name("post-tool-hook.ps1").read_text(encoding="utf-8")
    assert all(f"'{key}'" in resident_ps for key in nudge.NUDGE_ENV_KEYS)


def test_agenda_nudge_silent_when_worktree_lane_exists(tmp_path):
    home, cwd = _make_home(tmp_path)
    wt = tmp_path / "wt"
    (wt / ".effort-agenda" / "main").mkdir(parents=True)
    yaml_path = home / ".dotfiles" / "worktrees" / f"{_WID}.yaml"
    yaml_path.write_text(
        yaml_path.read_text("utf-8") + f"worktree_path: '{wt}'\n", encoding="utf-8",
    )
    assert not any(_has_agenda(o) for o in _agenda_runs(home, cwd, "expected"))


def test_agenda_nudge_silent_when_descriptor_has_agendas_placement(tmp_path):
    import json

    home, cwd = _make_home(tmp_path)
    descriptor = home / ".dotfiles" / "worktree-state" / _WID / "descriptor.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(json.dumps({"placements": [{"domain": "agendas"}]}), "utf-8")
    assert not any(_has_agenda(o) for o in _agenda_runs(home, cwd, "expected"))


def test_agenda_nudge_kill_switch(tmp_path):
    home, cwd = _make_home(tmp_path)
    env = {**_AGENDA_ENV, "AGENT_WORKTREES_AGENDA_NUDGE": "off"}
    assert not any(_has_agenda(o) for o in _agenda_runs(home, cwd, "expected", env=env))


@pytest.mark.parametrize("record", [None, "malformed", "wrong-version", "oversized"])
def test_agenda_nudge_fails_quiet_without_a_valid_published_record(tmp_path, record):
    home, cwd = _make_home(tmp_path)
    if record == "wrong-version":
        _publish_policy(home, "expected", version=2)
    elif record is not None:
        path = _publish_policy(home, "expected")
        path.write_text("{" if record == "malformed" else " " * 5000, "utf-8")
    assert not any(_has_agenda(o) for o in _agenda_runs(home, cwd, None))


def test_agenda_nudge_never_resolves_configuration(tmp_path, monkeypatch):
    """A slow or failing resolver can never stall the hook or monitor path."""
    import time as _time

    from agent_worktrees import config as cfg

    calls = []

    def slow(*args, **kwargs):
        calls.append(args)
        _time.sleep(30)
        raise AssertionError("configuration resolution must not run in the hook path")

    for name in ("load_config", "load_project_config"):
        if hasattr(cfg, name):
            monkeypatch.setattr(cfg, name, slow)
    home, cwd = _make_home(tmp_path)
    started = _time.monotonic()
    outs = _agenda_runs(home, cwd, "expected")
    assert _time.monotonic() - started < 5
    assert [i for i, o in enumerate(outs) if _has_agenda(o)] == [2, 5]
    assert calls == []


@pytest.mark.parametrize("published", [True, False])
def test_isolated_fallback_interpreter_delivers_published_agenda(tmp_path, published):
    """The deployed copy runs under bare system Python without the plugin runtime."""
    import json
    import os
    import shutil
    import subprocess
    import sys

    home, cwd = _make_home(tmp_path)
    if published:
        _publish_policy(home, "expected")
    script = tmp_path / "bin" / "nudge_status.py"
    script.parent.mkdir()
    shutil.copyfile(_SCRIPT, script)
    env = {
        "HOME": str(home), "USERPROFILE": str(home), "PATH": os.environ.get("PATH", ""),
        "AGENT_WORKTREES_NUDGE_CALLS": "1", "AGENT_WORKTREES_NUDGE_MINUTES": "999",
    }
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(script)], input=json.dumps(_payload(cwd)),
        capture_output=True, text=True, timeout=30, env=env, cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    text = json.loads(result.stdout)["additionalContext"]
    assert "tool call(s)" in text
    assert _has_agenda(text) is published
