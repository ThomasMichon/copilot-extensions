"""Golden-screenshot baseline for representative Worktrees-pivot states
(``worktrees-pivot-ux-overhaul`` effort, Phase 1).

Sibling to ``test_picker_capture.py``'s own golden coverage (a 2-row WIP/
UNUSED fleet) -- this file adds the specific representative states that
effort's Phase 1 plan calls for: empty, ACTIVE-only, a broader mixed
ACTIVE+Recent+various-state fleet, a claims-bearing (open PR) worktree, and a
long-running-but-recently-used worktree (the exact shape Phase 3's
recency-sort fix targets -- an old ``started_at`` next to live/recent
activity, captured here as the documented "before" state).

Same deterministic-renderer contract as the sibling file: a known fixture
fleet yields a known character grid, regenerate-able with
``AGENT_WORKTREES_UPDATE_GOLDENS=1``. Each golden is intentionally
self-contained (own fixture, own file) so a later phase's rendering change
shows a clean, reviewable diff scoped to the state it actually affects.
"""
from __future__ import annotations

import datetime
import os
import re
import types

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from worktree_manager.production_picker.picker_tui import capture as pcap  # noqa: E402
from worktree_manager.production_picker.picker_tui import derive  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "goldens", "picker")

_TOPBAR_RE = re.compile(r"^\s*Worktree Manager.*$")


def _isolate_pivots(monkeypatch, tmp_path):
    """Empty pivot + plugin dirs so no locally-installed contributed pivot
    leaks into the grid -- keeps the golden environment-neutral (mirrors
    ``test_picker_capture.py``'s own helper of the same name)."""
    pivots = tmp_path / "pivots"
    plugins = tmp_path / "plugins"
    pivots.mkdir()
    plugins.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(pivots))
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugins))


def _normalize(grid: str) -> str:
    lines = grid.splitlines()
    if lines:
        lines[0] = "<<TOPBAR>>" if _TOPBAR_RE.match(lines[0]) else lines[0].rstrip()
    return "\n".join(ln.rstrip() for ln in lines) + "\n"


def _golden(name: str, actual: str) -> str:
    path = os.path.join(GOLDEN_DIR, name)
    if os.environ.get("AGENT_WORKTREES_UPDATE_GOLDENS"):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(actual)
        return actual
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _source(raws: list[dict], *, local=("anomalous-potato", "Win")):
    """A hermetic fleet source over exactly ``raws`` (frozen clock; no git/
    SSH/subprocess) -- the same shape ``test_picker_capture.py``'s own
    ``_fixture_source`` builds, parameterized over the row set."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = f"{local[0]} · {local[1].lower()}"
    src.machines = lambda: [(f"{local[0]} {local[1]}", local[0], local[1], True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def _capture_grid(monkeypatch, tmp_path, raws: list[dict]) -> str:
    _isolate_pivots(monkeypatch, tmp_path)
    caps = pcap.capture(_source(raws), live=False)
    return _normalize(caps["text"])


def test_empty_worktrees_list_matches_golden(monkeypatch, tmp_path):
    """No worktrees at all -- the "nothing here yet" state."""
    grid = _capture_grid(monkeypatch, tmp_path, [])
    assert grid == _golden("scenario_empty.txt", grid)


def test_active_only_worktrees_list_matches_golden(monkeypatch, tmp_path):
    """A single, currently-attached live session -- no Recent/Completed rows
    at all."""
    raws = [
        {"id": "anomalous-potato-win-20260627-a001", "title": "Ship the release notes",
         "status": "active", "started_at": "2026-06-27T17:45:00",
         "turn_count": 12, "state": "wip", "ahead": 1, "behind": 0,
         "mux_attached": True, "mux_clients": 1},
    ]
    grid = _capture_grid(monkeypatch, tmp_path, raws)
    assert grid == _golden("scenario_active_only.txt", grid)


def test_mixed_worktrees_list_matches_golden(monkeypatch, tmp_path):
    """ACTIVE + a spread of Recent states (wip/dirty/unused/completed) --
    the everyday, most-representative shape of the Worktrees pivot."""
    raws = [
        {"id": "anomalous-potato-win-20260627-m001", "title": "Ship the release notes",
         "status": "active", "started_at": "2026-06-27T17:45:00",
         "turn_count": 12, "state": "wip", "ahead": 1, "behind": 0,
         "mux_attached": True, "mux_clients": 1},
        {"id": "anomalous-potato-win-20260627-m002", "title": "Fix the flaky test",
         "status": "active", "started_at": "2026-06-27T16:00:00",
         "turn_count": 3, "state": "dirty"},
        {"id": "anomalous-potato-win-20260626-m003", "title": "Old idle wt",
         "status": "active", "started_at": "2026-06-26T09:00:00",
         "turn_count": 0, "state": "unused"},
        {"id": "anomalous-potato-win-20260620-m004", "title": "Docs pass",
         "status": "finalized", "completed_at": "2026-06-25T12:00:00",
         "started_at": "2026-06-20T08:00:00", "turn_count": 6, "state": "clean"},
    ]
    grid = _capture_grid(monkeypatch, tmp_path, raws)
    assert grid == _golden("scenario_mixed.txt", grid)


def test_claims_worktree_matches_golden(monkeypatch, tmp_path):
    """A worktree carrying an open PR -- the PRs/claims column's non-empty
    state (Phase 4 target: standardize this as a CLAIMS column)."""
    raws = [
        {"id": "anomalous-potato-win-20260627-c001", "title": "Add the retry budget",
         "status": "active", "started_at": "2026-06-27T15:00:00",
         "turn_count": 8, "state": "wip", "ahead": 3, "behind": 0,
         "pr": {"number": 4821, "state": "open"}},
    ]
    grid = _capture_grid(monkeypatch, tmp_path, raws)
    assert grid == _golden("scenario_claims.txt", grid)


def test_long_running_worktree_matches_golden(monkeypatch, tmp_path):
    """A worktree started weeks ago, with substantial session history and a
    RECENT real resume (``last_resumed_at``), but NOT currently attached (so
    it lands in Recent, not Active) -- next to a worktree created yesterday
    that has never been touched since. Documents the Phase-3 fix: Recent
    orders by most-recently-used (``last_resumed_at``, falling back to
    ``started_at`` when a worktree has never been resumed), not by creation
    age -- so the long-running, heavily-used, recently-resumed row 1 sorts
    ABOVE the merely-newer-but-untouched row 2, even though row 1's
    ``started_at`` is far older. Before Phase 3 (no ``last_resumed_at``
    signal at all), row 2 would have sorted first purely by creation age;
    this golden is the corrected "after" baseline."""
    raws = [
        {"id": "anomalous-potato-win-20260528-l001", "title": "Long-haul migration",
         "status": "active", "started_at": "2026-05-28T09:00:00",
         "last_resumed_at": "2026-06-27T16:30:00",
         "turn_count": 47, "state": "wip", "ahead": 5, "behind": 2},
        {"id": "anomalous-potato-win-20260626-l002", "title": "Created yesterday, untouched",
         "status": "active", "started_at": "2026-06-26T17:00:00",
         "turn_count": 0, "state": "unused"},
    ]
    grid = _capture_grid(monkeypatch, tmp_path, raws)
    assert grid == _golden("scenario_long_running.txt", grid)
