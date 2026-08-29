"""End-to-end integration test for the E1e knowledge overlay (config-graft).

Flexes the full muscle of the ``.agent-*`` config graft the way a stateless
harness bound to a knowledge repo actually uses it: a **name-free harness base**
plus a **knowledge repo** that carries the real ``related.yaml`` (+ narratives)
and ``machines.yaml``. Drives the real CLI command (``cmd_related_dispatch``) and
the real ``config`` loaders end-to-end, asserting:

* ``related list/show/resolve/doc`` surface the knowledge repo's entries,
* a knowledge entry's narrative ``doc`` resolves **inside the knowledge repo**,
* ``machines.yaml`` redirects to the knowledge repo's topology, and
* the harness tree is **never written to** (stays name-free).

Mirrors the live dogfood against ``citadel-harness`` + ``citadel-knowledge-proto``
so the split has a reproducible regression guard independent of any machine state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import disposition_history as dh
from agent_worktrees import related
from agent_worktrees import state_root as sr
from agent_worktrees import tracking

# ---------------------------------------------------------------------------
# Fixtures: a name-free harness + a knowledge repo carrying the real config.
# ---------------------------------------------------------------------------

_HARNESS_RELATED = (
    "# name-free harness base\nprimary: null\nrelated: {}\n"
)
_KNOWLEDGE_RELATED = """\
primary: example-web
related:
  example-web:
    role: product
    summary: "Primary product monorepo."
    doc: related/example-web.md
    locus: { preferred: codespace }
    delegate: { via: agent-codespaces }
  copilot-extensions:
    role: tooling
    summary: "Source of the agent-* plugins."
    doc: related/copilot-extensions.md
    locus: { preferred: machine:cloud1, machines: [cloud1] }
    delegate: { via: agent-bridge }
"""
_KNOWLEDGE_MACHINES = (
    "control_plane:\n  project: harness\n"
    "machines:\n  example-cloud1:\n    display_name: cloud1\n    role: cloud-dev\n"
)


def _stateless_config(harness: Path, knowledge_repo="knowledge"):
    return cfg.Config(
        srcroot="/src", machine="test", platform="linux",
        repo_name="harness", knowledge_repo=knowledge_repo,
        repos={"harness": cfg.RepoConfig(
            anchor=str(harness), worktree_root=str(harness) + ".wt",
            default_branch="main", remote="origin", stateless=True)},
    )


@pytest.fixture
def split(tmp_path: Path, monkeypatch):
    """A bound (harness, knowledge) pair with the stateless config wired in."""
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    (harness / ".agent-worktrees").mkdir(parents=True)
    (harness / ".agent-worktrees" / "related.yaml").write_text(
        _HARNESS_RELATED, encoding="utf-8")
    kdir = knowledge / ".agent-worktrees"
    (kdir / "related").mkdir(parents=True)
    (kdir / "related.yaml").write_text(_KNOWLEDGE_RELATED, encoding="utf-8")
    (kdir / "related" / "example-web.md").write_text(
        "# example-web narrative\n", encoding="utf-8")
    (kdir / "related" / "copilot-extensions.md").write_text(
        "# copilot-extensions narrative\n", encoding="utf-8")
    (kdir / "machines.yaml").write_text(_KNOWLEDGE_MACHINES, encoding="utf-8")

    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda *args, **kwargs: _stateless_config(harness),
    )
    monkeypatch.setattr(
        sr, "_checkout_path",
        lambda name: str(knowledge) if name == "knowledge" else None)
    return harness, knowledge


# ---------------------------------------------------------------------------
# The config-source seam resolves to [harness base, knowledge overlay].
# ---------------------------------------------------------------------------

def test_config_sources_are_harness_then_knowledge(split):
    harness, knowledge = split
    srcs = sr.config_source_anchors(cfg.load_config(), base_anchor=str(harness))
    assert [(s.origin, s.anchor) for s in srcs] == [
        ("harness", str(harness)),
        ("knowledge", str(knowledge)),
    ]


# ---------------------------------------------------------------------------
# `related` CLI end-to-end through the graft.
# ---------------------------------------------------------------------------

def test_related_list_surfaces_knowledge_entries(split, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    assert run(["list", "--repo", str(harness), "--json"]) == 0
    out = json.loads(capfd.readouterr().out)
    assert out["primary"] == "example-web"
    names = {e["name"] for e in out["related"]}
    assert names == {"example-web", "copilot-extensions"}


def test_related_show_doc_resolves_in_knowledge(split, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, knowledge = split
    assert run(["show", "example-web", "--repo", str(harness)]) == 0
    out = capfd.readouterr().out
    # the narrative doc path points INTO the knowledge repo, not the harness
    assert str(knowledge) in out
    assert str(harness / ".agent-worktrees" / "related") not in out


def test_related_doc_scaffold_target_is_knowledge(split, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, knowledge = split
    assert run(["doc", "copilot-extensions", "--repo", str(harness)]) == 0
    printed = capfd.readouterr().out.strip().splitlines()[0]
    assert Path(printed) == (
        knowledge / ".agent-worktrees" / "related" / "copilot-extensions.md"
    )


def test_related_resolve_grafted_entry(split, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    assert run(["resolve", "example-web", "--repo", str(harness), "--json"]) == 0
    out = json.loads(capfd.readouterr().out)
    assert out["name"] == "example-web"


def test_related_conduct_merges_configured_and_related_corpora(
    split, monkeypatch, capfd
):
    from agent_worktrees import __main__ as cli

    harness, _ = split
    configured = _stateless_config(harness)
    configured.repos["injected-tool"] = cfg.RepoConfig(
        anchor="/injected",
        worktree_root="/injected.wt",
        pr=cfg.PRConfig(
            enabled=True,
            provider="github",
            strategy="keep-alive",
            merge_actor="submitter-direct",
        ),
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda *args, **kwargs: configured,
    )
    assert cli.cmd_related_dispatch(
        ["--conduct", "--repo", str(harness)]
    ) == 0
    out = capfd.readouterr().out
    assert "configured repos" not in out
    assert "`agent-worktrees repos list`" not in out
    assert "Registered repositories are discovered through the active project's" in out
    assert "repository tooling" in out
    assert "2 directional related entries" in out
    assert "`agent-worktrees related list`" in out
    assert "`agent-worktrees related show <repo>`" in out
    assert "`agent-worktrees related resolve <repo>`" in out
    assert "`agent-worktrees related doctor`" in out
    assert "4 repositories are available" not in out
    assert "class, locus, and delegate" in out
    assert "non-`none` delegate" in out
    assert "owning agent" in out
    assert "dispatch host's installed plugins" in out
    assert "`harness`" not in out
    assert "`example-web`" not in out
    assert len(out.rstrip()) <= 900


def test_related_conduct_omits_counts_without_directional_entries(
    split, monkeypatch, capfd
):
    from agent_worktrees import __main__ as cli

    harness, _ = split
    monkeypatch.setattr(
        related,
        "read_related_grafted",
        lambda anchors: related.RelatedConfig(),
    )
    assert cli.cmd_related_dispatch(
        ["--conduct", "--repo", str(harness)]
    ) == 0
    out = capfd.readouterr().out
    assert "Registered repositories are discovered through" in out
    assert "configured repos" not in out
    assert "directional related entries" not in out
    assert "`agent-worktrees repos list`" not in out
    assert "`agent-worktrees related list`" not in out


def test_typical_session_conduct_stays_within_context_budget(
    split, monkeypatch, capfd, tmp_path
):
    from agent_worktrees import __main__ as cli

    harness, knowledge = split
    configured = _stateless_config(harness)
    configured.repos["injected-tool"] = cfg.RepoConfig(
        anchor="/injected", worktree_root="/injected.wt"
    )
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda *args, **kwargs: configured,
    )

    assert cli.cmd_related_dispatch(
        ["--conduct", "--repo", str(harness)]
    ) == 0
    related_text = capfd.readouterr().out.strip()

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda: history_dir)
    record = tracking.WorktreeRecord(
        worktree_id="wt-budget",
        branch="b",
        worktree_path=str(harness),
        repo="harness",
        machine="test",
        platform="linux",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title="Conduct budget",
        status="active",
        completed_at=None,
    )
    tracking.save_record(record, history_dir / "wt-budget.yaml")
    summaries = [f"history-{i}-" + ("x" * 500) for i in range(8)]
    for i, summary in enumerate(summaries):
        dh.append(
            "wt-budget",
            at=f"2026-01-01T00:00:0{i}",
            summary=summary,
            title="Conduct budget",
            follow_up=True,
            changed=["summary"],
        )
    monkeypatch.setattr(
        cli, "_resolve_worktree_for_read",
        lambda worktree_id, worktree_dir, session_id: "wt-budget",
    )
    assert cli.cmd_history_digest(type("Args", (), {
        "worktree_id": "wt-budget",
        "worktree_dir": str(harness),
        "session_id": None,
        "limit": 8,
    })()) == 0
    history_text = capfd.readouterr().out.strip()
    assert len(history_text) <= dh.DIGEST_MAX_CHARS
    assert len(history_text) >= 700
    assert "history-7-" in history_text
    assert "..." in history_text
    assert dh.read("wt-budget")[-1]["summary"] == summaries[-1]

    record.sessions = [
        tracking.SessionEntry(session_id="active-session", started_at="t")
    ]
    record.head_session = "active-session"
    tracking.save_record(record, history_dir / "wt-budget.yaml")
    assert cli.cmd_history_digest(type("Args", (), {
        "worktree_id": "wt-budget",
        "worktree_dir": str(harness),
        "session_id": None,
        "limit": 8,
    })()) == 0
    history_with_succession = capfd.readouterr().out.strip()
    assert len(history_with_succession) <= dh.DIGEST_MAX_CHARS
    assert "Worktree succession" in history_with_succession
    assert "history-7-" in history_with_succession

    plugin = Path(__file__).parents[1]
    conduct = plugin / "scripts" / "conduct"
    from agent_worktrees.conduct import assemble_payload

    payload = assemble_payload(
        conduct,
        sr.state_repo_definition(sr.StateRoot(
            str(knowledge), "knowledge_repo", "knowledge",
            True, True, True,
        )),
        related_text,
        history_text,
    )
    assembled = json.loads(payload)["additionalContext"]
    assert len(payload) <= 4_000
    assert related_text in assembled
    assert "history-7-" in assembled
    if history_text not in assembled:
        assert "[Older worktree history omitted.]" in assembled


def test_session_conduct_forwards_discovered_project():
    """Each dynamic command is a fresh process, so related needs the cwd-gated
    project discovered by the hook rather than relying on ambient activation."""
    plugin = Path(__file__).parents[1]
    ps1 = (plugin / "scripts" / "session-conduct.ps1").read_text(
        encoding="utf-8"
    )
    sh = (plugin / "scripts" / "session-conduct.sh").read_text(encoding="utf-8")
    assert "-m agent_worktrees --project $project related --conduct" in ps1
    assert '-m agent_worktrees --project "$project" related --conduct' in sh
    assert "-m agent_worktrees history-digest" in ps1
    assert '-m agent_worktrees history-digest' in sh
    assert "-m agent_worktrees.conduct $dir" in ps1
    assert '-m agent_worktrees.conduct "$dir"' in sh
    assert ps1.index("state-root --conduct") < ps1.index("related --conduct")
    assert ps1.index("related --conduct") < ps1.index("history-digest")
    assert sh.index("state-root --conduct") < sh.index("related --conduct")
    assert sh.index("related --conduct") < sh.index("history-digest")


# ---------------------------------------------------------------------------
# machines.yaml redirect end-to-end.
# ---------------------------------------------------------------------------

def test_machines_redirect_to_knowledge(split):
    harness, knowledge = split
    assert cfg.machines_yaml_path(harness) == (
        knowledge / ".agent-worktrees" / "machines.yaml"
    )
    assert "example-cloud1" in cfg.load_machines_yaml(harness)


# ---------------------------------------------------------------------------
# The harness tree is never written to (stays name-free).
# ---------------------------------------------------------------------------

def test_harness_tree_stays_name_free(split, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    for argv in (["list", "--repo", str(harness), "--json"],
                 ["show", "example-web", "--repo", str(harness)],
                 ["resolve", "example-web", "--repo", str(harness), "--json"]):
        run(argv)
        capfd.readouterr()
    base = related.read_related(harness)
    assert base.related == {}  # no knowledge entries leaked into the base
    # the base related.yaml is byte-for-byte unchanged (graft never writes here)
    assert (harness / ".agent-worktrees" / "related.yaml").read_text(
        encoding="utf-8") == _HARNESS_RELATED
    # no narrative dir was created under the harness
    assert not (harness / ".agent-worktrees" / "related").exists()
