"""The real session hook checks the machine switch before any stamp mutation."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from test_session_stamp_security import _hook, _run
from stamp_fixture import no_shared_write_umask  # noqa: F401


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("fault", ["active", "missing-guard", "malformed-guard", "guard-error"])
def test_actual_hook_killswitch_precedes_all_runtime_writes(tmp_path, shell, fault):
    command, environment, repo = _hook(tmp_path, shell)
    # Shell startup caches are not Index artifacts; isolate them from the witness home.
    for kind in ("CACHE", "DATA", "CONFIG"):
        environment[f"XDG_{kind}_HOME"] = str(tmp_path / f"shell-{kind.lower()}")
    for key in ("APPDATA", "LOCALAPPDATA"):
        directory = tmp_path / f"shell-{key.lower()}"
        directory.mkdir()
        environment[key] = str(directory)
    state = Path(environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"])
    state.parent.mkdir()
    state.write_text(json.dumps({"active": True, "reason": "fixture-only pause"}))
    state_before = state.stat()
    home = Path(environment["HOME"])
    if os.name == "nt":
        # Windows shell startup initializes its profile directories independently
        # of APPDATA overrides; establish them before observing Index writes.
        (home / "AppData/Roaming").mkdir(parents=True)
    before_paths = set(p.relative_to(home) for p in home.rglob("*"))
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    suffix = "sh" if shell == "bash" else "ps1"
    guard = payload / "scripts" / f"bootstrap-killswitch-guard.{suffix}"
    if fault == "missing-guard":
        guard.unlink()
    elif fault == "malformed-guard":
        guard.write_text("if (\n", encoding="utf-8")
    elif fault == "guard-error":
        guard.write_text("exit 23\n", encoding="utf-8")
    result = _run(command, environment, repo)
    assert result.stdout == "{}"
    if fault == "active":
        assert result.returncode == 0, result.stderr
        assert "fixture-only pause" in result.stderr
    else:
        assert result.returncode != 0
        assert "bootstrap-killswitch guard" in result.stderr
        assert {"missing-guard": "missing", "malformed-guard": "malformed",
                "guard-error": "failed"}[fault] in result.stderr
    assert not Path(environment["AGENT_INDEX_HOME"]).exists()
    assert not (home / ".local").exists()
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    assert set(p.relative_to(home) for p in home.rglob("*")) == before_paths
    after = state.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_mode) == (
        state_before.st_ino, state_before.st_mtime_ns, state_before.st_mode,
    )


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_malformed_switch_state_retains_canonical_fail_open_policy(tmp_path, shell):
    command, environment, repo = _hook(tmp_path, shell)
    state = Path(environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"])
    state.parent.mkdir()
    state.write_text("not JSON")
    result = _run(command, environment, repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"
    assert (Path(environment["AGENT_INDEX_HOME"]) / "stamped-version").is_file()
    assert state.read_text() == "not JSON"


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_machine_switch_does_not_cross_namespaced_cell_boundary(tmp_path, shell):
    command, environment, repo = _hook(tmp_path, shell)
    environment["COPILOT_EXTENSIONS_CONTEXT"] = str(tmp_path / "unowned-context.json")
    state = Path(environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"])
    state.parent.mkdir()
    state.write_text('{"active":true,"reason":"fixture-only pause"}')
    result = _run(command, environment, repo)
    assert result.returncode != 0, "invalid cell must reach and fail its own admission"
    assert result.stdout == "{}"
    assert "fixture-only pause" not in result.stderr
    assert not Path(environment["AGENT_INDEX_HOME"]).exists()


@pytest.mark.parametrize("fault", ["throw", "missing", "child-exit"])
def test_powershell_runtime_gate_failures_use_hook_emitter(tmp_path, fault):
    command, environment, repo = _hook(tmp_path, "powershell")
    gate = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"]) / "scripts/runtime-gate.ps1"
    if fault == "missing":
        gate.unlink()
    else:
        gate.write_text(
            'throw "fixture runtime admission failed"\n' if fault == "throw" else
            "exit 23\n",
            encoding="utf-8",
        )
    result = _run(command, environment, repo)
    assert result.stdout == "{}"
    assert result.returncode == (23 if fault == "child-exit" else 1)
    if fault != "child-exit":
        assert "agent-index session runtime gate failed" in result.stderr
    if fault == "throw":
        assert "fixture runtime admission failed" in result.stderr
    assert not Path(environment["AGENT_INDEX_HOME"]).exists()
