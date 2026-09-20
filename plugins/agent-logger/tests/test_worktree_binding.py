"""Tests for worktree-binding derivation + sidecar marking (Phase 3,
session-worktree-archive-linkout).

Mirrors ``test_origin.py``'s shape and fixtures exactly.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from agent_logger.sync import worktree_binding as wb

HARNESS_ORIGIN = {
    "schema_version": 1,
    "machine": "book2",
    "source_repo": "test-chamber",
    "basis": "git_root",
}
MACHINE_ONLY_ORIGIN = {
    "schema_version": 1,
    "machine": "book2",
    "source_repo": None,
    "basis": "machine-default",
}


def _mk(root: Path, name: str, workspace: str | None) -> Path:
    d = root / "session-state" / name
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    if workspace is not None:
        (d / "workspace.yaml").write_text(workspace, encoding="utf-8")
    return d


def _install_fake_agent_worktrees(monkeypatch, *, by_cwd=None, by_session=None):
    """Install a fake ``agent_worktrees.tracking`` module in ``sys.modules``
    so :func:`worktree_binding._find_worktree_lookup`'s lazy import resolves
    to test doubles instead of requiring the real plugin installed."""
    by_cwd = by_cwd or {}
    by_session = by_session or {}

    def find_worktree_id_by_cwd(cwd: str, *, project: str | None = None):
        return by_cwd.get((cwd, project))

    def find_worktree_id_by_session(session_id: str, *, project: str | None = None):
        return by_session.get((session_id, project))

    fake_tracking = types.ModuleType("agent_worktrees.tracking")
    fake_tracking.find_worktree_id_by_cwd = find_worktree_id_by_cwd
    fake_tracking.find_worktree_id_by_session = find_worktree_id_by_session
    fake_pkg = types.ModuleType("agent_worktrees")
    monkeypatch.setitem(sys.modules, "agent_worktrees", fake_pkg)
    monkeypatch.setitem(sys.modules, "agent_worktrees.tracking", fake_tracking)


def _uninstall_agent_worktrees(monkeypatch):
    """Simulate ``agent-worktrees`` not being installed at all."""
    monkeypatch.setitem(sys.modules, "agent_worktrees", None)
    monkeypatch.setitem(sys.modules, "agent_worktrees.tracking", None)


def test_no_origin_is_unresolvable(tmp_path: Path, monkeypatch) -> None:
    _install_fake_agent_worktrees(monkeypatch)
    d = _mk(tmp_path, "s1", "cwd: /home/u/src/wt-a\n")
    assert wb.derive_worktree_binding(d, None) is None


def test_machine_only_origin_is_unresolvable(tmp_path: Path, monkeypatch) -> None:
    """A session with no resolved harness has no project to search --
    correctly unresolvable, never a guess."""
    _install_fake_agent_worktrees(monkeypatch)
    d = _mk(tmp_path, "s2", "cwd: /home/u/work/acme\n")
    assert wb.derive_worktree_binding(d, MACHINE_ONLY_ORIGIN) is None


def test_agent_worktrees_not_installed_is_unresolvable(
    tmp_path: Path, monkeypatch,
) -> None:
    """agent-worktrees is a soft dependency -- absent, every session is
    unresolvable (never an error)."""
    _uninstall_agent_worktrees(monkeypatch)
    d = _mk(tmp_path, "s3", "cwd: /home/u/src/test-chamber/wt-a\n")
    assert wb.derive_worktree_binding(d, HARNESS_ORIGIN) is None


def test_resolves_via_cwd(tmp_path: Path, monkeypatch) -> None:
    _install_fake_agent_worktrees(
        monkeypatch,
        by_cwd={("/home/u/src/test-chamber/wt-a", "test-chamber"): "wt-a"},
    )
    d = _mk(tmp_path, "s4", "cwd: /home/u/src/test-chamber/wt-a\n")
    binding = wb.derive_worktree_binding(d, HARNESS_ORIGIN)
    assert binding == {
        "schema_version": 1,
        "worktree_id": "wt-a",
        "confidence": "proactive",
        "basis": "cwd:cwd",
    }


def test_cwd_scoped_to_the_origins_source_repo(tmp_path: Path, monkeypatch) -> None:
    """The lookup is scoped to ``origin['source_repo']`` -- a match keyed to
    a *different* project must not be picked up."""
    _install_fake_agent_worktrees(
        monkeypatch,
        by_cwd={("/home/u/src/test-chamber/wt-a", "some-other-project"): "wt-a"},
    )
    d = _mk(tmp_path, "s5", "cwd: /home/u/src/test-chamber/wt-a\n")
    assert wb.derive_worktree_binding(d, HARNESS_ORIGIN) is None


def test_falls_back_to_git_root_when_cwd_unresolved(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_fake_agent_worktrees(
        monkeypatch,
        by_cwd={
            ("/home/u/src/test-chamber/wt-a", "test-chamber"): "wt-a",
        },
    )
    # cwd itself doesn't match (e.g. a subshell cd'd elsewhere), but git_root
    # does -- cwd is tried first and fails, git_root is tried next.
    d = _mk(
        tmp_path, "s6",
        "cwd: /home/u/scratch\ngit_root: /home/u/src/test-chamber/wt-a\n",
    )
    binding = wb.derive_worktree_binding(d, HARNESS_ORIGIN)
    assert binding is not None
    assert binding["worktree_id"] == "wt-a"
    assert binding["basis"] == "cwd:git_root"


def test_falls_back_to_session_id_when_no_cwd_match(
    tmp_path: Path, monkeypatch,
) -> None:
    """The identity fallback: a resumed session's recorded cwd no longer
    reflects its true worktree, but its session id was explicitly bound."""
    _install_fake_agent_worktrees(
        monkeypatch,
        by_session={("s7", "test-chamber"): "wt-resumed"},
    )
    d = _mk(tmp_path, "s7", "cwd: /home/u\n")  # HOME -- no cwd match anywhere
    binding = wb.derive_worktree_binding(d, HARNESS_ORIGIN)
    assert binding == {
        "schema_version": 1,
        "worktree_id": "wt-resumed",
        "confidence": "proactive",
        "basis": "session_id",
    }


def test_no_match_anywhere_is_unresolvable(tmp_path: Path, monkeypatch) -> None:
    _install_fake_agent_worktrees(monkeypatch)
    d = _mk(tmp_path, "s8", "cwd: /home/u/src/test-chamber/wt-gone\n")
    assert wb.derive_worktree_binding(d, HARNESS_ORIGIN) is None


def test_write_sidecar_idempotent(tmp_path: Path) -> None:
    d = tmp_path / "session-state" / "s9"
    d.mkdir(parents=True)
    binding = {
        "schema_version": 1, "worktree_id": "wt-a", "confidence": "proactive",
        "basis": "cwd:cwd",
    }
    assert wb.write_worktree_sidecar(d, binding) is True   # first write
    assert wb.write_worktree_sidecar(d, binding) is False  # unchanged
    written = json.loads((d / wb.WORKTREE_SIDECAR).read_text(encoding="utf-8"))
    assert written["worktree_id"] == "wt-a"


def test_read_sidecar_roundtrip_and_missing(tmp_path: Path) -> None:
    d = tmp_path / "session-state" / "s10"
    d.mkdir(parents=True)
    assert wb.read_worktree_sidecar(d) is None
    binding = {
        "schema_version": 1, "worktree_id": "wt-a", "confidence": "proactive",
        "basis": "cwd:cwd",
    }
    wb.write_worktree_sidecar(d, binding)
    back = wb.read_worktree_sidecar(d)
    assert back is not None
    assert back["worktree_id"] == "wt-a"
    (d / wb.WORKTREE_SIDECAR).write_text("not json", encoding="utf-8")
    assert wb.read_worktree_sidecar(d) is None


def test_mark_all_worktrees_writes_and_summarizes(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_fake_agent_worktrees(
        monkeypatch,
        by_cwd={("/home/u/src/test-chamber/wt-a", "test-chamber"): "wt-a"},
    )
    src = tmp_path / "copilot"
    resolvable = _mk(src, "resolvable", "cwd: /home/u/src/test-chamber/wt-a\n")
    unresolvable = _mk(src, "unresolvable", "cwd: /home/u/scratch\n")
    no_origin = _mk(src, "no-origin", "cwd: /home/u/src/test-chamber/wt-a\n")

    from agent_logger.sync.origin import write_origin_sidecar
    write_origin_sidecar(resolvable, HARNESS_ORIGIN)
    write_origin_sidecar(unresolvable, HARNESS_ORIGIN)
    # no_origin deliberately gets no origin.json -- simulates a sync pass
    # where origin marking somehow didn't run first.

    summary = wb.mark_all_worktrees(src)
    assert summary == {"total": 3, "marked": 1, "unresolved": 2}
    assert (resolvable / wb.WORKTREE_SIDECAR).is_file()
    assert not (unresolvable / wb.WORKTREE_SIDECAR).is_file()
    assert not (no_origin / wb.WORKTREE_SIDECAR).is_file()


def test_mark_all_worktrees_dry_run_counts_without_writing(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_fake_agent_worktrees(
        monkeypatch,
        by_cwd={("/home/u/src/test-chamber/wt-a", "test-chamber"): "wt-a"},
    )
    src = tmp_path / "copilot"
    resolvable = _mk(src, "resolvable", "cwd: /home/u/src/test-chamber/wt-a\n")
    from agent_logger.sync.origin import write_origin_sidecar
    write_origin_sidecar(resolvable, HARNESS_ORIGIN)

    summary = wb.mark_all_worktrees(src, dry_run=True)
    assert summary["total"] == 1
    assert summary["marked"] == 0  # dry-run never writes
    assert not (resolvable / wb.WORKTREE_SIDECAR).is_file()


def test_mark_all_worktrees_missing_source_dir(tmp_path: Path) -> None:
    summary = wb.mark_all_worktrees(tmp_path / "nonexistent")
    assert summary == {"total": 0, "marked": 0, "unresolved": 0}
