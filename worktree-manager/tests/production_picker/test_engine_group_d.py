from __future__ import annotations

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker import engine_group_d


def test_reap_orphan_mux_sessions_uses_group_d_contract(monkeypatch):
    payload = {"available": True, "reaped": ["wt-a"], "skipped": [], "errors": []}
    calls: list[tuple[str, list[str]]] = []

    def _run_json(project, args, **kwargs):
        calls.append((project, list(args)))
        return payload

    monkeypatch.setattr(engine_client, "run_json", _run_json)

    assert (
        engine_group_d.reap_orphan_mux_sessions(
            "dotfiles", worktree_ids=["wt-b", "wt-a"]
        )
        == payload
    )
    assert calls == [
        (
            "dotfiles",
            [
                "reap-sessions",
                "--json",
                "--include-manager-owned",
                "--worktree-id",
                "wt-b",
                "--worktree-id",
                "wt-a",
            ],
        )
    ]


def test_reap_orphan_mux_sessions_forwards_dry_run_and_grace(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda project, args, **kwargs: calls.append(list(args)) or {"available": True, "reaped": [], "skipped": [], "errors": []},
    )

    engine_group_d.reap_orphan_mux_sessions(
        "dotfiles",
        dry_run=True,
        idle_grace_secs=1800,
    )

    assert calls == [[
        "reap-sessions",
        "--json",
        "--include-manager-owned",
        "--dry-run",
        "--grace-hours",
        "0.5",
    ]]


@pytest.mark.parametrize(
    ("fn", "verb", "args"),
    [
        (
            engine_group_d.reap_orphan_launcher_shells,
            "reap-shells",
            ["reap-shells", "--json", "--yes"],
        ),
        (
            engine_group_d.sweep_managed_worktrees,
            "sweep-managed",
            ["sweep-managed", "--json"],
        ),
        (
            engine_group_d.sweep_finished_session_worktrees,
            "sweep-finished-sessions",
            ["sweep-finished-sessions", "--json"],
        ),
    ],
)
def test_group_d_verbs_classify_unsupported_engine(monkeypatch, fn, verb, args):
    def _boom(*_args, **_kwargs):
        raise engine_client.EngineError(
            f"agent-worktrees {' '.join(args)} failed "
            f"(exit 2): invalid choice: '{verb}'"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        fn("dotfiles")
