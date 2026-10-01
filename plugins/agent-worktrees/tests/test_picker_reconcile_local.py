from __future__ import annotations

import json
import types
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import picker_reconcile_cli


class _PR:
    def __init__(self, number: int, state: str) -> None:
        self.number = number
        self.state = state


class _Record:
    def __init__(
        self,
        worktree_id: str,
        worktree_path: Path,
        *,
        prs: list[_PR] | None = None,
        bound_live: bool | None = None,
        mux_live: bool | None = None,
    ) -> None:
        self.worktree_id = worktree_id
        self.worktree_path = str(worktree_path)
        self.prs = prs or []
        self.bound_live = bound_live
        self.mux_live = mux_live

    def active_pr(self):
        return self.prs[0] if self.prs else None


def test_picker_reconcile_local_json_reports_group_c_payload(
    monkeypatch,
    capfd,
    tmp_path,
):
    rec1 = _Record(
        "wt-one",
        tmp_path / "wt-one",
        prs=[_PR(17, "open")],
        bound_live=False,
        mux_live=False,
    )
    rec2 = _Record(
        "wt-two",
        tmp_path / "wt-two",
        prs=[],
        bound_live=True,
        mux_live=True,
    )
    Path(rec1.worktree_path).mkdir()
    Path(rec2.worktree_path).mkdir()

    monkeypatch.setattr(picker_reconcile_cli.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    monkeypatch.setattr(picker_reconcile_cli.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(picker_reconcile_cli.cfg, "load_config", lambda: object())
    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "list_records",
        lambda *args, **kwargs: [rec1, rec2],
    )
    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "_pr_is_terminal",
        lambda pr: getattr(pr, "state", "") in {"merged", "closed"},
    )
    monkeypatch.setattr(
        picker_reconcile_cli.pr_ops,
        "_pr_to_dict",
        lambda pr: {"number": pr.number, "state": pr.state},
    )

    def _reconcile(record, _config, *, best_effort=False):
        assert best_effort is True
        record.prs[0].state = "merged"

    monkeypatch.setattr(picker_reconcile_cli.pr_ops, "_reconcile_active_pr", _reconcile)
    monkeypatch.setattr(
        picker_reconcile_cli.reclaim,
        "resolve_bound_copilots",
        lambda: [{"worktree_id": "wt-one"}],
    )
    monkeypatch.setattr(
        picker_reconcile_cli.sessions,
        "mux_status_many",
        lambda ids: {
            wid: types.SimpleNamespace(
                exists=(wid == "wt-one"),
                clients=2 if wid == "wt-one" else 0,
                attached=(wid == "wt-one"),
            )
            for wid in ids
        },
    )
    monkeypatch.setattr(
        picker_reconcile_cli.sessions,
        "worktree_session_lock_state",
        lambda rec: (rec.worktree_id == "wt-one", [] if rec.worktree_id == "wt-one" else [404]),
    )
    monkeypatch.setattr(picker_reconcile_cli, "_fresh_bound_live_hint", lambda rec: rec.bound_live)

    bound_calls: list[tuple[str, bool, bool]] = []
    mux_calls: list[tuple[str, bool, bool]] = []

    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "stamp_bound_live",
        lambda worktree_id, live, refresh=False: bound_calls.append(
            (worktree_id, live, refresh)
        ),
    )
    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "stamp_mux_live",
        lambda worktree_id, live, refresh=False, sync=False: mux_calls.append(
            (worktree_id, live, sync)
        ),
    )

    rc = picker_reconcile_cli.cmd_picker_reconcile_local(
        types.SimpleNamespace(json=True, worktree_id=["wt-one", "wt-two"])
    )

    payload = json.loads(capfd.readouterr().out)
    assert rc == 0
    assert payload["summary"] == {
        "platform": "windows",
        "requested_worktree_ids": ["wt-one", "wt-two"],
        "record_count": 2,
        "pr_terminal_count": 1,
        "bound_visible_change_count": 2,
        "had_unresolved_bound": False,
        "mux_scan_ok": True,
    }
    assert payload["rows"] == [
        {
            "id": "wt-one",
            "pr": {"number": 17, "state": "merged"},
            "prs": [{"number": 17, "state": "merged"}],
            "pr_count": 1,
            "session_bound_live": True,
            "session_lock_live": True,
            "mux_session": True,
            "mux_clients": 2,
            "mux_attached": True,
        },
        {
            "id": "wt-two",
            "session_lock_stale": True,
            "stale_lock_pids": [404],
            "mux_session": False,
            "mux_clients": 0,
            "mux_attached": False,
        },
    ]
    assert bound_calls == [("wt-one", True, True), ("wt-two", False, False)]
    assert mux_calls == [("wt-one", True, True), ("wt-two", False, True)]


def test_picker_reconcile_local_skips_load_config_when_no_pr_to_reconcile(
    monkeypatch, tmp_path
):
    """Render-perf follow-up (#3307, 2026-09-30): ``cfg.load_config()`` pulls
    in a full, unbounded tracking-records re-scan plus plugin-activation
    resolution -- ~10s+ on a machine with a large tracking history, the
    dominant cost of a single-worktree reconcile (the Picker's per-row
    Actions-dialog refine). Profiling found it was paid in full even when no
    record in scope had a PR left to reconcile, so ``config`` went unused.
    A record with NO pr, and one with an already-terminal pr, must never
    trigger ``load_config()`` at all."""
    rec_no_pr = _Record("wt-none", tmp_path / "wt-none", prs=[])
    rec_terminal_pr = _Record(
        "wt-merged", tmp_path / "wt-merged", prs=[_PR(5, "merged")]
    )
    Path(rec_no_pr.worktree_path).mkdir()
    Path(rec_terminal_pr.worktree_path).mkdir()

    monkeypatch.setattr(picker_reconcile_cli.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    monkeypatch.setattr(picker_reconcile_cli.cfg, "detect_platform", lambda: "windows")

    def _forbidden_load_config():
        raise AssertionError(
            "load_config() must not be called when nothing needs PR reconcile"
        )

    monkeypatch.setattr(picker_reconcile_cli.cfg, "load_config", _forbidden_load_config)
    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "list_records",
        lambda *args, **kwargs: [rec_no_pr, rec_terminal_pr],
    )
    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "_pr_is_terminal",
        lambda pr: getattr(pr, "state", "") in {"merged", "closed"},
    )
    monkeypatch.setattr(
        picker_reconcile_cli.pr_ops,
        "_pr_to_dict",
        lambda pr: {"number": pr.number, "state": pr.state},
    )
    monkeypatch.setattr(
        picker_reconcile_cli.reclaim, "resolve_bound_copilots", lambda: []
    )
    monkeypatch.setattr(
        picker_reconcile_cli.sessions,
        "mux_status_many",
        lambda ids: {wid: types.SimpleNamespace(exists=False, clients=0, attached=False)
                     for wid in ids},
    )
    monkeypatch.setattr(
        picker_reconcile_cli.sessions,
        "worktree_session_lock_state",
        lambda rec: (False, []),
    )

    payload = picker_reconcile_cli.build_payload()

    assert payload["summary"]["record_count"] == 2
    assert payload["summary"]["pr_terminal_count"] == 0


def test_picker_reconcile_local_avoids_batch_wide_record_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(picker_reconcile_cli.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    monkeypatch.setattr(picker_reconcile_cli.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(
        picker_reconcile_cli.tracking,
        "list_records",
        lambda *args, **kwargs: [],
    )

    class _ForbiddenBatchLock:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            raise AssertionError("picker-reconcile-local must not hold one batch-wide record lock")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(picker_reconcile_cli.tracking, "_RecordLock", _ForbiddenBatchLock)

    payload = picker_reconcile_cli.build_payload()

    assert payload["summary"]["record_count"] == 0


def test_lazy_dispatch_exposes_picker_reconcile_local():
    assert (
        m._LAZY_DISPATCH_TABLE["picker-reconcile-local"]
        == ("picker_reconcile_cli", "cmd_picker_reconcile_local")
    )
