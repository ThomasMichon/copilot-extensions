"""Tests for ``sessions.verify_worktree_active`` (issue #4057).

The authoritative single-worktree liveness verdict used at the Actions-menu /
Enter moments. Unlike the batched populate scan it combines mux presence with
the cwd-independent ``inuse.<pid>.lock`` binding (via reclaim), so it also
catches a **bare** (un-muxed) bound Copilot the mux fleet view cannot see.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path
import pytest


import types
from unittest.mock import patch

from agent_worktrees import sessions, tracking, locks
import platform
from typing import cast


def _rec(wt_id: str="aaaa") -> tracking.WorktreeRecord:
    return cast(tracking.WorktreeRecord, types.SimpleNamespace(worktree_id=wt_id, worktree_path="/tmp/wt"))


def _bound(sid: str, homing: str) -> dict[str, Any]:
    return {"session_id": sid, "pid": 123, "cwd": "/tmp/wt",
            "worktree_id": "aaaa", "homing": homing}


def test_mux_only_is_inactive_source_mux() -> None:
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=True, clients=1)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots", return_value=[]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is False
    assert v.mux_live is True and v.mux_clients == 1
    assert v.live_session_ids == [] and v.bare is False
    assert v.source == "mux"
    assert v.probes_ok is True


def test_bare_lock_only_is_active_source_lock() -> None:
    """A bare bound Copilot with no mux is still ACTIVE (the case the mux fleet
    view misses); source is 'lock' and bare is flagged."""
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=False, clients=0)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-1", "bare")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True
    assert v.mux_live is False
    assert v.live_session_ids == ["sid-1"]
    assert v.bare is True
    assert v.source == "lock"
    assert v.probes_ok is True


def test_both_signals() -> None:
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=True, clients=2)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-1", "mux")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True and v.source == "both"
    assert v.mux_live is True and v.live_session_ids == ["sid-1"]
    assert v.bare is False
    assert v.probes_ok is True


def test_nothing_live_is_inactive() -> None:
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=False, clients=0)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots", return_value=[]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is False and v.source == "none"
    assert v.mux_live is False and v.live_session_ids == []
    assert v.probes_ok is True


def test_dedupes_and_sorts_session_ids() -> None:
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=False)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-2", "bare"), _bound("sid-1", "mux"),
                             _bound("sid-2", "bare")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.live_session_ids == ["sid-1", "sid-2"]
    assert v.bare is True  # at least one bare binding present
    assert v.probes_ok is True


def test_degrades_when_reclaim_raises(caplog: pytest.LogCaptureFixture) -> None:
    """Mux occupancy cannot manufacture Copilot activity after a failed probe."""
    with patch("agent_worktrees.sessions.mux_status_many",
               return_value={"aaaa": sessions.MuxInfo(exists=True, clients=0)}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               side_effect=RuntimeError("boom")):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is False and v.mux_live is True
    assert v.live_session_ids == [] and v.source == "mux"
    assert v.probes_ok is False  # the reclaim probe raised
    # The MUX probe itself succeeded (conclusively found a live mux) --
    # narrower than the aggregate `probes_ok` above, which also goes False
    # here purely because of the UNRELATED reclaim failure.
    assert v.mux_probe_ok is True
    assert "Could not verify registered Copilot bindings: boom" in caplog.text


def test_degrades_when_mux_raises(caplog: pytest.LogCaptureFixture) -> None:
    with patch("agent_worktrees.sessions.mux_status_many",
               side_effect=OSError("blocked")), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[_bound("sid-1", "bare")]):
        v = sessions.verify_worktree_active(_rec())
    assert v.active is True and v.mux_live is False
    assert v.source == "lock" and v.bare is True
    assert v.probes_ok is False  # the mux probe raised
    assert v.mux_probe_ok is False  # the mux probe itself is what raised here
    assert "Could not verify worktree mux presence: blocked" in caplog.text


def test_verification_passes_only_the_authoritative_session_registry() -> None:
    record = _rec()
    record.sessions = [tracking.SessionEntry(session_id="registered-session", started_at="")]
    with patch("agent_worktrees.sessions.mux_status_many", return_value={}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots", return_value=[]) as probe:
        verdict = sessions.verify_worktree_active(record)
    probe.assert_called_once_with(
        worktree_id="aaaa", worktree_path="/tmp/wt", session_ids=["registered-session"],
    )
    assert verdict.probes_ok


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("unknown_first", [False, True])
def test_unavailable_session_preserves_other_verified_live_bindings(strict: bool, unknown_first: bool) -> None:
    from agent_worktrees.push_timeout import ObservationUnavailable

    record = _rec()
    ids = ["unknown", "live"] if unknown_first else ["live", "unknown"]
    record.sessions = [tracking.SessionEntry(session_id=sid, started_at="") for sid in ids]

    def resolve(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["session_ids"] == ["unknown"]:
            raise ObservationUnavailable("Registered session working directory is unavailable")
        return [_bound("live", "bare")]

    with patch("agent_worktrees.sessions.mux_status_many", return_value={}), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots", side_effect=resolve):
        verdict = sessions.verify_worktree_active(record, strict=strict)
    assert verdict.active and verdict.bare and verdict.live_session_ids == ["live"]
    assert verdict.source == "lock" and not verdict.probes_ok and verdict.mux_probe_ok


def test_live_registered_lock_requires_matching_session_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from agent_worktrees import process_table_cache, reclaim

    state = tmp_path / "session-state"
    entry = state / "registered-session"
    entry.mkdir(parents=True)
    (entry / "inuse.123.lock").touch()
    workspace = entry / "workspace.yaml"
    workspace.write_text(json.dumps({"cwd": str(tmp_path / "other")}), encoding="utf-8")
    record = _rec()
    record.worktree_path = str(tmp_path / "worktree")
    record.sessions = [tracking.SessionEntry(session_id="registered-session", started_at="")]
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
    monkeypatch.setattr(sessions, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr(sessions, "_is_copilot_process", lambda pid: True)
    monkeypatch.setattr(sessions, "mux_status_many", lambda *_a, **_k: {})
    monkeypatch.setattr(process_table_cache, "cached", lambda *_a: {})
    monkeypatch.setattr(reclaim, "homing_of", lambda *_a: "mux")
    monkeypatch.setattr(locks, "process_start_time", lambda _pid: "123")

    assert sessions.worktree_session_lock_state(record)[0] is False
    assert sessions.session_id_is_live(record, "registered-session") is False
    assert sessions.scan_sessions_fast([record]).active_sessions == {}
    assert sessions.verify_worktree_active(record).active is False

    workspace.write_text(json.dumps({"cwd": record.worktree_path}), encoding="utf-8")
    assert sessions.worktree_session_lock_state(record)[0] is True
    assert sessions.session_id_is_live(record, "registered-session") is True
    assert sessions.verify_worktree_active(record).active is True

    workspace.unlink()
    verdict = sessions.verify_worktree_active(record)
    assert not verdict.active and not verdict.probes_ok
    from agent_worktrees import resume_seed

    assert resume_seed.cold_start_error(verdict) is not None


def test_windows_session_cwd_normalizes_case_separators_and_parent_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import reclaim

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert reclaim._cwd_under("C:/SOURCE/owned/child", r"c:\source\owned")
    assert not reclaim._cwd_under(r"C:\source\owned-sibling", r"c:\source\owned")
    assert not reclaim._cwd_under(r"C:\source\owned\..\other", r"c:\source\owned")


def test_fleet_discovery_ignores_unrelated_missing_cwd_but_scoped_binding_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from agent_worktrees import reclaim
    from agent_worktrees.push_timeout import ObservationUnavailable

    for sid in ("a-unrelated", "b-owned"):
        entry = tmp_path / sid
        entry.mkdir()
        (entry / "inuse.123.lock").touch()
    (tmp_path / "b-owned" / "workspace.yaml").write_text(
        json.dumps({"cwd": "/tmp/wt"}), encoding="utf-8",
    )
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    monkeypatch.setattr(sessions, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(sessions, "_is_copilot_process", lambda _pid: True)
    monkeypatch.setattr(reclaim, "_resolve_worktree_id_for_cwd", lambda _cwd: "aaaa")
    monkeypatch.setattr(reclaim, "homing_of", lambda *_a: "mux")
    monkeypatch.setattr(locks, "process_start_time", lambda _pid: "123")
    found = reclaim.resolve_bound_copilots(worktree_id="aaaa", worktree_path="/tmp/wt", table={})
    assert [binding["session_id"] for binding in found] == ["b-owned"]
    with pytest.raises(ObservationUnavailable):
        reclaim.resolve_bound_copilots(
            worktree_id="aaaa", worktree_path="/tmp/wt", session_ids=["a-unrelated"], table={},
        )


@pytest.mark.parametrize(("case", "expected"), [
    pytest.param("native", True, id="native_cli"), ("script", True), ("foreign", False),
    ("denied", None), ("malformed", None), ("oversized", None), ("truncated", None),
    ("foreign_checkout", False), ("foreign_executable", False), ("foreign_argument", False),
    ("node_foreign_entry", False), ("node_symlink", True), ("node_option", None), ("node_relative", None),
])
def test_darwin_registered_activity_uses_bounded_native_process_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, expected: bool | None,
) -> None:
    import ctypes
    import json
    import os
    import posixpath
    import sys
    from agent_worktrees import process_table_cache, reclaim, session_liveness

    loader = "/usr/local/lib/node_modules/@github/copilot/npm-loader.js"
    commands = {
        "script": ["/usr/local/bin/node", loader],
        "foreign": ["/usr/bin/python", "worker.py"],
        "foreign_checkout": ["/usr/bin/python3", "/checkout/copilot-extensions/tools/worker.py"],
        "foreign_executable": ["/opt/copilot-support/bin/copilot-helper", "worker.py"],
        "foreign_argument": ["/usr/bin/python3", "worker.py", "--note=copilot"],
        "node_foreign_entry": ["/usr/local/bin/node", "/checkout/copilot-extensions/worker.js", loader],
        "node_symlink": ["/usr/local/bin/node", "/usr/local/bin/copilot"],
        "node_option": ["/usr/local/bin/node", "-e", f"require('{loader}')"],
        "node_relative": ["/usr/local/bin/node", "node_modules/@github/copilot/npm-loader.js"],
    }
    argv = commands.get(case, ["/usr/local/bin/copilot", "--resume=example"])
    argc = 0 if case == "malformed" else len(argv)
    payload = argc.to_bytes(4, sys.byteorder, signed=True) + argv[0].encode() + b"\0\0"
    payload += b"\0".join(arg.encode() for arg in argv) + b"\0"
    payload = payload[:-1] if case == "truncated" else payload + b"DISPLAY_NOTE=copilot\0"

    def sysctl(mib: Any, count: int, output: Any, size_pointer: Any, _new: Any, _new_size: int) -> int:
        assert list(mib) == [1, 49, 123] and count == 3
        if case == "denied":
            return -1
        size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_size_t))
        if output is None:
            size.contents.value = (1 << 20) + 1 if case == "oversized" else len(payload)
        else:
            assert case != "oversized", "oversized process data must not be allocated or read"
            ctypes.memmove(output, payload, len(payload))
            size.contents.value = len(payload)
        return 0

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: types.SimpleNamespace(sysctl=sysctl))
    monkeypatch.setattr(os, "kill", lambda *_a: None)
    original_realpath = os.path.realpath

    def realpath(path: Any, *args: Any, **kwargs: Any) -> str:
        if path == argv[1]:
            return loader if case == "node_symlink" else posixpath.normpath(path)
        return cast(str, original_realpath(path, *args, **kwargs))

    monkeypatch.setattr(os.path, "realpath", realpath)
    assert session_liveness.copilot_pid_state(123) is expected
    assert sessions._is_copilot_process(123) is (expected is True)
    record = _rec()
    record.worktree_path = str(tmp_path / "worktree")
    record.sessions = [tracking.SessionEntry(session_id="registered", started_at="")]
    entry = tmp_path / "registered"
    entry.mkdir()
    (entry / "inuse.123.lock").touch()
    (entry / "workspace.yaml").write_text(json.dumps({"cwd": record.worktree_path}), encoding="utf-8")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    monkeypatch.setattr(sessions, "_is_process_alive", lambda _pid: True)
    monkeypatch.setattr(sessions, "mux_status_many", lambda *_a, **_kw: {})
    monkeypatch.setattr(process_table_cache, "cached", lambda *_a: {})
    monkeypatch.setattr(reclaim, "homing_of", lambda *_a: "unknown")
    monkeypatch.setattr(reclaim, "_resolve_worktree_id_for_cwd", lambda _cwd: "aaaa")
    monkeypatch.setattr(locks, "process_start_time", lambda _pid: "123")
    ctx = sessions.scan_sessions_fast([record])
    norm = sessions._normalize_path(record.worktree_path)
    assert bool(ctx.active_sessions.get(norm)) is (expected is True)
    assert (norm in ctx.unavailable_paths) is (expected is None)
    verdict = sessions.verify_worktree_active(record)
    assert verdict.active is (expected is True)
    assert verdict.probes_ok is (expected is not None)
    discovered = reclaim.resolve_bound_copilots(
        worktree_id="aaaa", worktree_path=record.worktree_path, table={},
    )
    assert bool(discovered) is (expected is True)
