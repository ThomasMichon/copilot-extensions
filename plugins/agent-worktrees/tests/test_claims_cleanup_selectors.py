"""Typed cleanup selectors cannot broaden the orphanage mutation target."""

from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import cleanup, orphan_receiving, tracking
from agent_worktrees import config as cfg
from agent_worktrees.tracking_claims import ResourceClaim


@pytest.fixture
def orphanage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> cfg.Config:
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="m",
        platform="windows",
        repo_name="p",
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name or 'p'}")
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    (tmp_path / ".p").mkdir()
    # Provider dispatch is isolated here; receiving proofs run in test_orphanage_identity.
    monkeypatch.setattr(
        orphan_receiving,
        "claim_guard",
        lambda *a, **k: nullcontext(True),
    )
    monkeypatch.setattr(
        cleanup,
        "_run_codespaces",
        lambda *a, **k: subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"reclaimed": True}),
            stderr="",
        ),
    )
    return config


def _rehome(config: cfg.Config, owner: str, *refs: str) -> None:
    tracking.rehome_abandoned_obligations(
        [ResourceClaim(kind="codespace", ref=ref) for ref in refs],
        source_worktree=owner,
        config=config,
    )


def test_typed_worktree_filter_does_not_match_an_unrelated_resource_ref(
    orphanage: cfg.Config,
) -> None:
    _rehome(orphanage, "owner-a", "cs-selected", "cs-other")
    _rehome(orphanage, "owner-b", "owner-a")

    rows = cleanup.cleanup_orphanage(
        orphanage,
        apply=False,
        source_worktrees={"owner-a"},
        refs={"cs-selected"},
    )

    assert [(row["source_worktree"], row["ref"]) for row in rows] == [
        ("owner-a", "cs-selected"),
    ]
    assert len(tracking.load_orphaned_obligations()) == 3


def test_named_filters_preserve_unselected_obligations(
    orphanage: cfg.Config,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _rehome(orphanage, "owner-a", "cs-a")
    _rehome(orphanage, "owner-b", "cs-b")
    args = main.build_parser().parse_args(
        [
            "claims",
            "cleanup",
            "--worktree-id",
            "owner-a",
            "--claim-ref",
            "cs-a",
            "--apply",
            "--json",
        ]
    )

    assert main.cmd_claims(args) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["worktree_ids"] == ["owner-a"]
    assert payload["claim_refs"] == ["cs-a"]
    assert [row["ref"] for row in payload["results"]] == ["cs-a"]
    assert [entry["ref"] for entry in tracking.load_orphaned_obligations()] == ["cs-b"]


def test_unmatched_filter_does_not_report_an_empty_orphanage(
    orphanage: cfg.Config,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _rehome(orphanage, "owner-a", "cs-kept")
    args = main.build_parser().parse_args(["claims", "cleanup", "--worktree-id", "owner-b"])

    assert main.cmd_claims(args) == 0
    printed = capfd.readouterr().out
    assert "no re-homed obligations matched" in printed
    assert "orphanage is empty" not in printed
    assert len(tracking.load_orphaned_obligations()) == 1
