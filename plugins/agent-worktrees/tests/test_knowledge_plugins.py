"""Focused tests for paired harness/knowledge plugin composition."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as main
from agent_worktrees import knowledge_plugins as kp


def _concurrent_compose_worker(
    harness_path: str,
    knowledge_path: str,
    iterations: int,
    barrier,
) -> None:
    """Spawn-safe overlay writer used on both Windows and POSIX."""
    for _ in range(iterations):
        barrier.wait(timeout=60)
        kp.compose(harness_path, knowledge_path, pair_id="pair-1")


def _write_settings(
    repo: Path, data: dict, *, local: bool = False, claude: bool = False
) -> None:
    directory = repo / ".claude" if claude else repo / ".github" / "copilot"
    directory.mkdir(parents=True, exist_ok=True)
    name = "settings.local.json" if local else "settings.json"
    (directory / name).write_text(json.dumps(data), encoding="utf-8")


def _read_overlay(harness: Path) -> dict:
    return json.loads(
        (harness / ".github" / "copilot" / "settings.local.json").read_text(
            encoding="utf-8"
        )
    )


def _pair_resolution(
    harness: Path, knowledge: Path, *, knowledge_repo: str = "private"
):
    return kp.state_root.StatePair(
        paired=True,
        pair_id="pair-1",
        pair_ref="machine/private/wt-k",
        pair_kind="worktree",
        current=kp.state_root.PairCheckout(
            role="harness",
            path=str(harness),
            repo="harness",
            worktree_id="wt-h",
        ),
        sibling=kp.state_root.PairCheckout(
            role="knowledge",
            path=str(knowledge),
            repo=knowledge_repo,
            worktree_id="wt-k",
        ),
    )


def test_paired_compose_uses_knowledge_worktree_and_carries_remote(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness-worktree"
    knowledge = tmp_path / "knowledge-worktree"
    harness.mkdir()
    (knowledge / ".ai").mkdir(parents=True)
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "generic": {
                    "source": {"source": "github", "repo": "org/generic"}
                }
            },
            "enabledPlugins": {"base@generic": True},
        },
    )
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                },
                "operator-remote": {
                    "source": {"source": "github", "repo": "me/plugins"}
                },
                "generic": {
                    "source": {"source": "github", "repo": "org/generic"}
                },
            },
            "enabledPlugins": {
                "notes@personal": True,
                "special@operator-remote": True,
                "base@generic": True,
            },
        },
    )
    _write_settings(
        harness,
        {
            "theme": "operator-choice",
            "extraKnownMarketplaces": {
                "unmanaged": {
                    "source": {"source": "github", "repo": "me/unmanaged"}
                }
            },
            "enabledPlugins": {"mine@unmanaged": True},
        },
        local=True,
    )
    resolution = _pair_resolution(harness, knowledge)
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    summary = kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo="private")
    )
    overlay = _read_overlay(harness)

    assert summary["paired"] is True
    local_path = overlay["extraKnownMarketplaces"]["personal"]["source"]["path"]
    assert Path(local_path) == (knowledge / ".ai").resolve()
    assert overlay["extraKnownMarketplaces"]["operator-remote"]["source"]["repo"] == (
        "me/plugins"
    )
    assert overlay["enabledPlugins"]["special@operator-remote"] is True
    assert overlay["enabledPlugins"]["notes@personal"] is True
    # Generic base entries stay in settings.json instead of being duplicated.
    assert "generic" not in overlay["extraKnownMarketplaces"]
    assert "base@generic" not in overlay["enabledPlugins"]
    # Unmanaged local data is untouched.
    assert overlay["theme"] == "operator-choice"
    assert overlay["enabledPlugins"]["mine@unmanaged"] is True


def test_repointed_knowledge_retires_old_managed_entries(tmp_path: Path):
    harness = tmp_path / "harness"
    first = tmp_path / "knowledge-one"
    second = tmp_path / "knowledge-two"
    harness.mkdir()
    (first / ".ai").mkdir(parents=True)
    (second / ".ai").mkdir(parents=True)
    _write_settings(
        first,
        {
            "extraKnownMarketplaces": {
                "old": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"old-skill@old": True},
        },
    )
    _write_settings(
        second,
        {
            "extraKnownMarketplaces": {
                "new": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"new-skill@new": True},
        },
    )
    _write_settings(harness, {"editor": "unmanaged"}, local=True)

    kp.compose(harness, first)
    kp.compose(harness, second)
    overlay = _read_overlay(harness)

    assert "old" not in overlay["extraKnownMarketplaces"]
    assert "old-skill@old" not in overlay["enabledPlugins"]
    assert Path(overlay["extraKnownMarketplaces"]["new"]["source"]["path"]) == (
        second / ".ai"
    ).resolve()
    assert overlay["enabledPlugins"]["new-skill@new"] is True
    assert overlay["editor"] == "unmanaged"


def test_same_pair_recompose_converges_advanced_source_and_preserves_unmanaged(
    tmp_path: Path,
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "operator": {
                    "source": {"source": "github", "repo": "example/old"}
                }
            },
            "enabledPlugins": {"old@operator": True},
        },
    )
    _write_settings(
        harness,
        {
            "theme": "unmanaged",
            "enabledPlugins": {"local-choice@example": False},
        },
        local=True,
    )
    kp.compose(
        harness,
        knowledge,
        pair_id="pair-1",
        pair_kind="worktree",
    )

    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "operator": {
                    "source": {"source": "github", "repo": "example/current"}
                }
            },
            "enabledPlugins": {"current@operator": True},
        },
    )
    summary = kp.compose(
        harness,
        knowledge,
        pair_id="pair-1",
        pair_kind="worktree",
    )
    overlay = _read_overlay(harness)

    assert summary["changed"] is True
    assert overlay["extraKnownMarketplaces"]["operator"]["source"]["repo"] == (
        "example/current"
    )
    assert "old@operator" not in overlay["enabledPlugins"]
    assert overlay["enabledPlugins"]["current@operator"] is True
    assert overlay["enabledPlugins"]["local-choice@example"] is False
    assert overlay["theme"] == "unmanaged"


def test_legacy_anchor_overlay_is_adopted_and_repointed_to_pair(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    anchor = tmp_path / "knowledge-anchor"
    worktree = tmp_path / "knowledge-worktree"
    harness.mkdir()
    (anchor / ".ai").mkdir(parents=True)
    (worktree / ".ai").mkdir(parents=True)
    _write_settings(
        anchor,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"stale@personal": True},
        },
    )
    _write_settings(
        worktree,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"current@personal": True},
        },
    )
    # Shape written by the old skill assembler: no ownership marker, anchor
    # path, and a stale enable from the same local marketplace.
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {
                        "source": "directory",
                        "path": (anchor / ".ai").resolve().as_posix(),
                    }
                },
                "unmanaged": {
                    "source": {"source": "github", "repo": "me/plugins"}
                },
            },
            "enabledPlugins": {
                "stale@personal": True,
                "mine@unmanaged": True,
            },
        },
        local=True,
    )

    resolution = _pair_resolution(harness, worktree)
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)
    monkeypatch.setattr(kp.repos_mod, "resolve_path", lambda _name: str(anchor))

    kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo="private")
    )
    overlay = _read_overlay(harness)
    assert Path(
        overlay["extraKnownMarketplaces"]["personal"]["source"]["path"]
    ) == (worktree / ".ai").resolve()
    assert overlay["enabledPlugins"]["stale@personal"] is True
    assert overlay["enabledPlugins"]["current@personal"] is True
    assert overlay["enabledPlugins"]["mine@unmanaged"] is True


def test_markerless_operator_local_marketplace_and_extra_enable_are_preserved(
    tmp_path: Path,
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    operator = tmp_path / "operator-marketplace"
    harness.mkdir()
    (knowledge / ".ai").mkdir(parents=True)
    (operator / ".ai").mkdir(parents=True)
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"current@personal": True},
        },
    )
    operator_definition = {
        "source": {
            "source": "directory",
            "path": (operator / ".ai").resolve().as_posix(),
        }
    }
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {"personal": operator_definition},
            "enabledPlugins": {"operator-extra@personal": True},
        },
        local=True,
    )

    # Merely pointing at a possible anchor path is insufficient proof: the
    # anchor's own settings do not declare this marketplace.
    summary = kp.compose(
        harness, knowledge, legacy_knowledge_path=operator
    )
    overlay = _read_overlay(harness)

    assert overlay["extraKnownMarketplaces"]["personal"] == operator_definition
    assert overlay["enabledPlugins"]["operator-extra@personal"] is True
    assert "current@personal" not in overlay["enabledPlugins"]
    assert summary["conflicts"] == {
        "marketplaces": ["personal"],
        "enabled_plugins": [
            "current@personal",
            "operator-extra@personal",
        ],
    }


def test_markerless_legacy_false_disable_is_preserved(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    (knowledge / ".ai").mkdir(parents=True)
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"current@personal": True},
        },
    )
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {
                        "source": "directory",
                        "path": (knowledge / ".ai").resolve().as_posix(),
                    }
                }
            },
            "enabledPlugins": {"current@personal": False},
        },
        local=True,
    )

    summary = kp.compose(harness, knowledge)
    overlay = _read_overlay(harness)

    assert overlay["enabledPlugins"]["current@personal"] is False
    assert summary["conflicts"]["enabled_plugins"] == ["current@personal"]
    assert summary["enabled_plugins"] == []


def test_markerless_legacy_stale_extra_enable_is_preserved(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    (knowledge / ".ai").mkdir(parents=True)
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"current@personal": True},
        },
    )
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {
                        "source": "directory",
                        "path": (knowledge / ".ai").resolve().as_posix(),
                    }
                }
            },
            "enabledPlugins": {
                "current@personal": True,
                "stale@personal": True,
            },
        },
        local=True,
    )

    summary = kp.compose(harness, knowledge)
    overlay = _read_overlay(harness)

    assert overlay["enabledPlugins"]["current@personal"] is True
    assert overlay["enabledPlugins"]["stale@personal"] is True
    assert summary["enabled_plugins"] == ["current@personal"]
    assert summary["conflicts"]["enabled_plugins"] == ["stale@personal"]


def test_marked_overlay_preserves_modified_and_unmanaged_local_namespace(
    tmp_path: Path,
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    (knowledge / ".ai" / "personal").mkdir(parents=True)
    (knowledge / ".ai" / "other").mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "directory", "path": "./.ai/personal"}
                },
                "other": {
                    "source": {"source": "directory", "path": "./.ai/other"}
                },
            },
            "enabledPlugins": {
                "notes@personal": True,
                "tasks@other": True,
            },
        },
    )

    kp.compose(harness, knowledge, pair_id="pair-1")
    overlay = _read_overlay(harness)
    operator_path = (tmp_path / "operator-marketplace").resolve().as_posix()
    overlay["extraKnownMarketplaces"]["personal"]["source"]["path"] = operator_path
    overlay["enabledPlugins"]["tasks@other"] = False
    overlay["enabledPlugins"]["operator-added@other"] = True
    _write_settings(harness, overlay, local=True)

    summary = kp.compose(harness, knowledge, pair_id="pair-1")
    overlay = _read_overlay(harness)

    assert overlay["extraKnownMarketplaces"]["personal"]["source"]["path"] == (
        operator_path
    )
    assert overlay["enabledPlugins"]["tasks@other"] is False
    assert overlay["enabledPlugins"]["operator-added@other"] is True
    assert summary["conflicts"]["marketplaces"] == ["personal"]
    assert summary["conflicts"]["enabled_plugins"] == [
        "notes@personal",
        "tasks@other",
    ]


def test_unmanaged_collision_is_preserved(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "mine": {"source": {"source": "github", "repo": "knowledge/plugins"}}
            },
            "enabledPlugins": {"skill@mine": True},
        },
    )
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "mine": {"source": {"source": "github", "repo": "local/override"}}
            },
            "enabledPlugins": {"skill@mine": False},
        },
        local=True,
    )

    summary = kp.compose(harness, knowledge)
    overlay = _read_overlay(harness)
    assert overlay["extraKnownMarketplaces"]["mine"]["source"]["repo"] == (
        "local/override"
    )
    assert overlay["enabledPlugins"]["skill@mine"] is False
    assert summary["conflicts"]["marketplaces"] == ["mine"]
    assert summary["conflicts"]["enabled_plugins"] == ["skill@mine"]


def test_claude_local_marketplace_and_disable_are_conflicts(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    (knowledge / ".ai").mkdir(parents=True)
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "remote": {
                    "source": {"source": "github", "repo": "knowledge/plugins"}
                },
                "personal": {
                    "source": {"source": "directory", "path": "./.ai"}
                },
            },
            "enabledPlugins": {
                "remote-skill@remote": True,
                "local-skill@personal": True,
            },
        },
    )
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "remote": {
                    "source": {"source": "github", "repo": "operator/plugins"}
                }
            },
            "enabledPlugins": {"local-skill@personal": False},
        },
        local=True,
        claude=True,
    )

    summary = kp.compose(harness, knowledge)
    overlay = _read_overlay(harness)

    assert "remote" not in overlay.get("extraKnownMarketplaces", {})
    assert overlay["extraKnownMarketplaces"]["personal"]["source"]["path"] == (
        knowledge / ".ai"
    ).resolve().as_posix()
    assert "local-skill@personal" not in overlay.get("enabledPlugins", {})
    assert summary["conflicts"]["marketplaces"] == ["remote"]
    assert summary["conflicts"]["enabled_plugins"] == [
        "local-skill@personal",
        "remote-skill@remote",
    ]


def test_native_committed_settings_override_claude_local_conflicts(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    desired = {"source": {"source": "github", "repo": "knowledge/plugins"}}
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {"mine": desired},
            "enabledPlugins": {"skill@mine": True},
        },
    )
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {
                "mine": {"source": {"source": "github", "repo": "claude/plugins"}}
            },
            "enabledPlugins": {"skill@mine": False},
        },
        local=True,
        claude=True,
    )
    _write_settings(
        harness,
        {
            "extraKnownMarketplaces": {"mine": desired},
            "enabledPlugins": {"skill@mine": True},
        },
    )

    summary = kp.compose(harness, knowledge)

    assert summary["conflicts"] == {
        "marketplaces": [],
        "enabled_plugins": [],
    }
    assert summary["count"] == 0


def test_unpaired_resolution_is_successful_noop_without_writing(
    tmp_path: Path, monkeypatch
):
    resolution = kp.state_root.StatePair(
        paired=False, error="current directory is not a tracked worktree"
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)
    summary = kp.compose_from_pair(
        cwd=tmp_path, config=SimpleNamespace(knowledge_repo="private")
    )
    assert summary == {
        "action": "no-op",
        "paired": False,
        "retired": False,
        "changed": False,
        "pair_error": "current directory is not a tracked worktree",
    }
    assert not (tmp_path / ".github" / "copilot" / "settings.local.json").exists()


def test_stale_pair_retires_exact_managed_overlay(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {"source": {"source": "github", "repo": "me/plugins"}}
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    kp.compose(harness, knowledge, pair_id="pair-1", pair_kind="worktree")
    resolution = kp.state_root.StatePair(
        paired=True,
        pair_id="pair-1",
        current=kp.state_root.PairCheckout(
            role="harness",
            path=str(harness),
            repo="harness",
            worktree_id="wt-h",
        ),
        error="paired sibling 'machine/private/wt-k' has no local record",
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    summary = kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo="private")
    )
    assert summary["action"] == "retired"
    assert summary["pair_error"] == (
        "paired sibling 'machine/private/wt-k' has no local record"
    )
    assert summary["retired_entries"] == {
        "marketplaces": ["personal"],
        "enabled_plugins": ["notes@personal"],
    }
    assert summary["file_removed"] is True
    assert not (harness / ".github" / "copilot" / "settings.local.json").exists()
    assert (
        kp.compose_from_pair(
            cwd=harness, config=SimpleNamespace(knowledge_repo="private")
        )["action"]
        == "no-op"
    )


def test_removed_pair_retires_overlay_only_for_tracked_stateless_harness(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {"source": {"source": "github", "repo": "me/plugins"}}
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    kp.compose(harness, knowledge, pair_id="removed-pair", pair_kind="worktree")
    resolution = kp.state_root.StatePair(
        paired=False,
        current=kp.state_root.PairCheckout(
            role="",
            path=str(harness),
            repo="harness",
            worktree_id="wt-h",
        ),
        error="worktree 'wt-h' is not paired",
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)
    config = SimpleNamespace(
        knowledge_repo="private",
        repos={"harness": SimpleNamespace(stateless=True)},
    )

    summary = kp.compose_from_pair(cwd=harness, config=config)
    assert summary["action"] == "retired"
    assert summary["pair_error"] == "worktree 'wt-h' is not paired"
    assert summary["file_removed"] is True


@pytest.mark.parametrize(
    ("binding", "knowledge_repo", "message"),
    [
        ("new-private", "old-private", "no longer matches"),
        ("", "private", "non-empty live knowledge_repo binding"),
    ],
)
def test_mismatch_or_unbound_pair_retires_managed_overlay(
    tmp_path: Path,
    monkeypatch,
    binding: str,
    knowledge_repo: str,
    message: str,
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge-old"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {"source": {"source": "github", "repo": "me/plugins"}}
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    kp.compose(harness, knowledge, pair_id="pair-1", pair_kind="worktree")
    resolution = _pair_resolution(
        harness, knowledge, knowledge_repo=knowledge_repo
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    summary = kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo=binding)
    )
    assert summary["action"] == "retired"
    assert message in summary["pair_error"]
    assert summary["file_removed"] is True


@pytest.mark.parametrize(
    ("binding", "knowledge_repo", "message"),
    [
        ("", "private", "non-empty live knowledge_repo binding"),
        ("private", "", "no repo identity"),
    ],
)
def test_pair_requires_live_binding_and_knowledge_identity(
    tmp_path: Path,
    monkeypatch,
    binding: str,
    knowledge_repo: str,
    message: str,
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    resolution = _pair_resolution(
        harness, knowledge, knowledge_repo=knowledge_repo
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    summary = kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo=binding)
    )
    assert summary["action"] == "no-op"
    assert message in summary["pair_error"]
    assert not (harness / ".github" / "copilot" / "settings.local.json").exists()


def test_stale_retirement_preserves_modified_managed_value(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {"source": {"source": "github", "repo": "me/plugins"}}
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    kp.compose(harness, knowledge, pair_id="pair-1", pair_kind="worktree")
    overlay = _read_overlay(harness)
    operator_value = {
        "source": {"source": "github", "repo": "operator/replacement"}
    }
    overlay["extraKnownMarketplaces"]["personal"] = operator_value
    overlay["theme"] = "operator-choice"
    _write_settings(harness, overlay, local=True)
    resolution = kp.state_root.StatePair(
        paired=True,
        pair_id="pair-1",
        current=kp.state_root.PairCheckout(
            role="harness",
            path=str(harness),
            repo="harness",
            worktree_id="wt-h",
        ),
        error="paired sibling disappeared",
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    summary = kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo="private")
    )
    overlay = _read_overlay(harness)
    assert summary["action"] == "retired"
    assert summary["preserved_modified"]["marketplaces"] == ["personal"]
    assert overlay["extraKnownMarketplaces"]["personal"] == operator_value
    assert overlay["theme"] == "operator-choice"
    assert "notes@personal" not in overlay.get("enabledPlugins", {})
    assert "_agentWorktreesKnowledgePluginOverlay" not in overlay


def test_unpaired_harness_does_not_retire_anchor_overlay(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge-anchor"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {"source": {"source": "github", "repo": "me/plugins"}}
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    kp.compose(harness, knowledge)
    before = _read_overlay(harness)
    resolution = kp.state_root.StatePair(
        paired=False,
        current=kp.state_root.PairCheckout(
            role="harness",
            path=str(harness),
            repo="harness",
            worktree_id="wt-h",
        ),
        error="worktree 'wt-h' is not paired",
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    summary = kp.compose_from_pair(
        cwd=harness, config=SimpleNamespace(knowledge_repo="private")
    )
    assert summary["action"] == "no-op"
    assert summary["retired"] is False
    assert _read_overlay(harness) == before


def test_stale_pair_with_malformed_overlay_is_hard_failure(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    output.write_text("{ malformed", encoding="utf-8")
    resolution = kp.state_root.StatePair(
        paired=True,
        pair_id="pair-1",
        current=kp.state_root.PairCheckout(
            role="harness",
            path=str(harness),
            repo="harness",
            worktree_id="wt-h",
        ),
        error="paired sibling disappeared",
    )
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    with pytest.raises(kp.KnowledgePluginError, match="cannot read existing"):
        kp.compose_from_pair(
            cwd=harness, config=SimpleNamespace(knowledge_repo="private")
        )
    assert output.read_text(encoding="utf-8") == "{ malformed"


def test_config_load_failure_preserves_pair_owned_overlay(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {"source": {"source": "github", "repo": "me/plugins"}}
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    kp.compose(harness, knowledge, pair_id="pair-1", pair_kind="worktree")
    output = harness / ".github" / "copilot" / "settings.local.json"
    before = output.read_bytes()
    resolution = _pair_resolution(harness, knowledge)
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    def _fail_config(*_args, **_kwargs):
        raise OSError("config denied")

    monkeypatch.setattr(kp.cfg, "load_config", _fail_config)

    with pytest.raises(kp.KnowledgePluginError, match="config denied"):
        kp.compose_from_pair(cwd=harness)
    assert output.read_bytes() == before


def test_cli_json_distinguishes_safe_noop_from_unsanitized_error(
    monkeypatch, capsys
):
    safe = {
        "action": "no-op",
        "paired": False,
        "retired": False,
        "changed": False,
        "pair_error": "ordinary repo is not paired",
    }
    monkeypatch.setattr(kp, "compose_from_pair", lambda **_kwargs: safe)
    assert main.cmd_knowledge_dispatch(["compose-plugins", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == safe

    def _fail(**_kwargs):
        raise kp.KnowledgePluginError("unsafe malformed overlay")

    monkeypatch.setattr(kp, "compose_from_pair", _fail)
    assert main.cmd_knowledge_dispatch(["compose-plugins", "--json"]) == 3
    assert json.loads(capsys.readouterr().out) == {
        "action": "error",
        "paired": False,
        "sanitized": False,
        "error": "unsafe malformed overlay",
    }


def test_pair_config_loads_by_harness_record_for_bare_resume(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    resolution = _pair_resolution(harness, knowledge)
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)
    monkeypatch.setattr(kp.cfg, "project_dir", lambda name: tmp_path / f".{name}")
    loaded_paths: list[Path] = []

    def _load(path):
        loaded_paths.append(path)
        return SimpleNamespace(knowledge_repo="private")

    monkeypatch.setattr(kp.cfg, "load_config", _load)

    pair = kp.resolve_pair(cwd=harness)
    assert pair.harness_path == harness.resolve()
    assert loaded_paths == [tmp_path / ".harness" / "config.yaml"]


def test_existing_malformed_local_settings_are_never_replaced(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    output.write_text("{ malformed", encoding="utf-8")
    knowledge.mkdir()

    with pytest.raises(kp.KnowledgePluginError, match="cannot read existing"):
        kp.compose(harness, knowledge)
    assert output.read_text(encoding="utf-8") == "{ malformed"


def test_explicit_compose_rejects_identical_checkout_paths_before_reading(
    tmp_path: Path,
):
    checkout = tmp_path / "checkout"
    source = checkout / ".github" / "copilot" / "settings.json"
    source.parent.mkdir(parents=True)
    source.write_text("{ malformed", encoding="utf-8")

    with pytest.raises(
        kp.KnowledgePairIntegrityError, match="resolve to the same path"
    ):
        kp.compose(checkout, checkout / ".." / "checkout")

    assert source.read_text(encoding="utf-8") == "{ malformed"
    assert not (source.parent / "settings.local.json").exists()


def test_pair_rejects_identical_checkout_paths_without_retiring_overlay(
    tmp_path: Path, monkeypatch
):
    checkout = tmp_path / "checkout"
    output = checkout / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)
    resolution = _pair_resolution(checkout, checkout / ".." / "checkout")
    monkeypatch.setattr(kp.state_root, "resolve_pair", lambda *_a, **_k: resolution)

    with pytest.raises(
        kp.KnowledgePairIntegrityError, match="resolve to the same path"
    ):
        kp.compose_from_pair(
            cwd=checkout, config=SimpleNamespace(knowledge_repo="private")
        )

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "relative_path",
    [
        Path(".claude/settings.json"),
        Path(".claude/settings.local.json"),
        Path(".github/copilot/settings.json"),
        Path(".github/copilot/settings.local.json"),
    ],
)
def test_malformed_knowledge_source_tier_preserves_overlay(
    tmp_path: Path, relative_path: Path
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    source = knowledge / relative_path
    source.parent.mkdir(parents=True)
    source.write_text("{ malformed", encoding="utf-8")
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)

    with pytest.raises(kp.KnowledgePluginError, match="knowledge source settings"):
        kp.compose(harness, knowledge)

    assert output.read_bytes() == before


def test_unreadable_knowledge_source_preserves_overlay(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    source = knowledge / ".github" / "copilot" / "settings.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)
    original_read_text = Path.read_text

    def _read_text(path: Path, *args, **kwargs):
        if path == source:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    with pytest.raises(kp.KnowledgePluginError, match="denied"):
        kp.compose(harness, knowledge)

    assert output.read_bytes() == before


@pytest.mark.parametrize("source_kind", ["directory", "local"])
@pytest.mark.parametrize("raw_path", [None, 42, " \t"])
def test_invalid_local_marketplace_path_preserves_overlay(
    tmp_path: Path, source_kind: str, raw_path
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    source = knowledge / ".github" / "copilot" / "settings.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "personal": {
                        "source": {"source": source_kind, "path": raw_path}
                    }
                },
                "enabledPlugins": {"notes@personal": True},
            }
        ),
        encoding="utf-8",
    )
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)

    with pytest.raises(
        kp.KnowledgePluginError, match=r"source.path.*non-empty string"
    ):
        kp.compose(harness, knowledge)

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "source_definition",
    [
        None,
        [],
        {},
        {"source": None},
        {"source": " \t"},
    ],
)
def test_invalid_marketplace_source_shape_preserves_overlay(
    tmp_path: Path, source_definition
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    source = knowledge / ".github" / "copilot" / "settings.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "personal": {"source": source_definition}
                },
                "enabledPlugins": {"notes@personal": True},
            }
        ),
        encoding="utf-8",
    )
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)

    with pytest.raises(kp.KnowledgePluginError, match="marketplace 'personal'"):
        kp.compose(harness, knowledge)

    assert output.read_bytes() == before


def test_malformed_legacy_source_preserves_markerless_overlay(tmp_path: Path):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    legacy = tmp_path / "legacy"
    harness.mkdir()
    knowledge.mkdir()
    source = legacy / ".claude" / "settings.json"
    source.parent.mkdir(parents=True)
    source.write_text("{ malformed", encoding="utf-8")
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)

    with pytest.raises(
        kp.KnowledgePluginError, match="legacy knowledge source settings"
    ):
        kp.compose(harness, knowledge, legacy_knowledge_path=legacy)

    assert output.read_bytes() == before


@pytest.mark.parametrize(
    "relative_path",
    [
        Path(".claude/settings.local.json"),
        Path(".github/copilot/settings.json"),
    ],
)
def test_malformed_harness_source_tier_preserves_overlay(
    tmp_path: Path, relative_path: Path
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    source = harness / relative_path
    source.parent.mkdir(parents=True)
    source.write_text("{ malformed", encoding="utf-8")
    output = harness / ".github" / "copilot" / "settings.local.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    before = b'{"operator": "unchanged"}\n'
    output.write_bytes(before)

    with pytest.raises(kp.KnowledgePluginError, match="harness source settings"):
        kp.compose(harness, knowledge)

    assert output.read_bytes() == before


def test_main_explicit_paths_compose_from_neutral_directory(
    tmp_path: Path, monkeypatch, capsys
):
    neutral = tmp_path / "neutral"
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    neutral.mkdir()
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": {
                "personal": {
                    "source": {"source": "github", "repo": "me/plugins"}
                }
            },
            "enabledPlugins": {"notes@personal": True},
        },
    )
    monkeypatch.chdir(neutral)

    rc = main.main(
        [
            "knowledge",
            "compose-plugins",
            "--harness-path",
            str(harness),
            "--knowledge-path",
            str(knowledge),
            "--json",
        ]
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["action"] == "composed"
    assert _read_overlay(harness)["enabledPlugins"]["notes@personal"] is True


def test_main_neutral_cwd_pair_mode_activates_path_project(
    tmp_path: Path, monkeypatch, capsys
):
    neutral = tmp_path / "neutral"
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    neutral.mkdir()
    harness.mkdir()
    knowledge.mkdir()
    _write_settings(knowledge, {"enabledPlugins": {"notes@personal": True}})
    resolution = _pair_resolution(harness, knowledge)
    monkeypatch.chdir(neutral)

    def _git_toplevel(path):
        assert path == harness
        return harness

    monkeypatch.setattr(main, "_git_toplevel", _git_toplevel)
    monkeypatch.setattr(main, "_reverse_lookup_project", lambda _anchor: "harness")

    def _resolve(config=None, *, cwd=None):
        assert main.cfg.active_project() == "harness"
        assert Path(cwd) == harness
        return resolution

    monkeypatch.setattr(kp.state_root, "resolve_pair", _resolve)
    monkeypatch.setattr(
        kp.cfg,
        "load_config",
        lambda *_a, **_k: SimpleNamespace(knowledge_repo="private"),
    )

    rc = main.main(
        ["knowledge", "compose-plugins", "--cwd", str(harness), "--json"]
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["paired"] is True
    assert main.cfg.active_project() == "harness"


def test_main_default_pair_mode_activates_process_cwd(
    tmp_path: Path, monkeypatch, capsys
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    resolution = _pair_resolution(harness, knowledge)
    monkeypatch.chdir(harness)

    def _git_toplevel(path):
        assert path == harness
        return harness

    monkeypatch.setattr(main, "_git_toplevel", _git_toplevel)
    monkeypatch.setattr(main, "_reverse_lookup_project", lambda _anchor: "harness")

    def _resolve(config=None, *, cwd=None):
        assert main.cfg.active_project() == "harness"
        assert Path(cwd) == harness
        return resolution

    monkeypatch.setattr(kp.state_root, "resolve_pair", _resolve)
    monkeypatch.setattr(
        kp.cfg,
        "load_config",
        lambda *_a, **_k: SimpleNamespace(knowledge_repo="private"),
    )

    rc = main.main(["knowledge", "compose-plugins", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["paired"] is True
    assert main.cfg.active_project() == "harness"


def test_knowledge_no_project_surface_preserves_explicit_project(
    tmp_path: Path, monkeypatch, capsys
):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    resolution = _pair_resolution(harness, knowledge)
    monkeypatch.setattr(
        main, "_resolve_active_project", lambda _name: ("launcher-project", None)
    )

    def _resolve(config=None, *, cwd=None):
        assert main.cfg.active_project() == "launcher-project"
        return resolution

    monkeypatch.setattr(kp.state_root, "resolve_pair", _resolve)
    monkeypatch.setattr(
        kp.cfg,
        "load_config",
        lambda *_a, **_k: SimpleNamespace(knowledge_repo="private"),
    )

    rc = main.main(
        [
            "--project",
            "launcher-project",
            "knowledge",
            "compose-plugins",
            "--cwd",
            str(harness),
            "--json",
        ]
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["paired"] is True
    assert main.cfg.active_project() == "launcher-project"


def test_concurrent_compose_is_cross_process_atomic(tmp_path: Path):
    import multiprocessing as mp

    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    marketplaces = {
        f"market-{index}": {
            "source": {
                "source": "github",
                "repo": f"owner/plugins-{index}",
            }
        }
        for index in range(200)
    }
    enabled = {
        f"skill-{index}@market-{index}": True
        for index in range(200)
    }
    _write_settings(
        knowledge,
        {
            "extraKnownMarketplaces": marketplaces,
            "enabledPlugins": enabled,
        },
    )
    _write_settings(
        harness,
        {"theme": "operator-choice"},
        local=True,
    )

    workers = 4
    iterations = 5
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(workers)
    processes = [
        ctx.Process(
            target=_concurrent_compose_worker,
            args=(
                str(harness),
                str(knowledge),
                iterations,
                barrier,
            ),
        )
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
        assert process.exitcode == 0, f"compose worker exited {process.exitcode}"

    overlay = _read_overlay(harness)
    marker = overlay["_agentWorktreesKnowledgePluginOverlay"]
    assert overlay["theme"] == "operator-choice"
    assert overlay["extraKnownMarketplaces"] == marketplaces
    assert overlay["enabledPlugins"] == enabled
    assert marker["marketplaces"] == marketplaces
    assert marker["enabledPlugins"] == enabled
    output_dir = harness / ".github" / "copilot"
    assert list(output_dir.glob(".settings.local.json.*.tmp")) == []


def test_launchers_compose_after_plan_before_copilot_handoff():
    root = Path(__file__).resolve().parents[1]
    sh = (root / "bin" / "launch-session.sh").read_text(encoding="utf-8")
    ps = (root / "bin" / "launch-session.ps1").read_text(encoding="utf-8")

    sh_compose = sh.index("_KNOWLEDGE_ARGS+=(knowledge compose-plugins")
    assert sh.index('cd "$WORK_DIR"') < sh_compose
    assert sh_compose < sh.index('if [[ "$NO_MUX" == "1" ]]')
    sh_refresh = sh.index('_REFRESHED_PYTHON="$(resolve_runtime_python)"')
    assert sh.rfind("invoke_update_apply 1 1", 0, sh_refresh) < sh_refresh
    assert sh_refresh < sh_compose
    assert 'PYTHON="$_REFRESHED_PYTHON"' in sh[sh_refresh:sh_compose]
    assert "runtime is unavailable after update apply" in sh[sh_refresh:sh_compose]
    assert '"${_KNOWLEDGE_ARGS[@]}" 2>&1' in sh
    assert 'exit "$_KNOWLEDGE_RC"' in sh
    assert "Knowledge plugin preflight failed" in sh
    assert sh_compose < sh.index('PANE_CMD=("${CLEAN_ENV[@]}"')
    assert sh_compose < sh.index('"${CLEAN_ENV[@]}" "${CMD_ARRAY[@]}"')

    ps_compose = ps.index("'knowledge', 'compose-plugins'")
    assert ps.index("Set-Location $plan.work_dir") < ps_compose
    assert ps_compose < ps.index("# Apply environment variables from the launch plan")
    ps_refresh = ps.index("$refreshedVenvPython = Resolve-RuntimePython")
    assert ps.rfind("Invoke-UpdateApply", 0, ps_refresh) < ps_refresh
    assert ps_refresh < ps_compose
    assert "$VenvPython = $refreshedVenvPython" in ps[ps_refresh:ps_compose]
    assert "runtime is unavailable after update apply" in ps[ps_refresh:ps_compose]
    assert "$knowledgeOutput = & $VenvPython @knowledgeArgs 2>&1" in ps
    assert "exit $knowledgeExit" in ps
    assert "Knowledge plugin preflight failed" in ps
    assert ps_compose < ps.index("& $cmd[0] $cmd[1..($cmd.Count - 1)]")
