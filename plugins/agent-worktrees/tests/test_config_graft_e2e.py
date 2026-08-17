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
from agent_worktrees import related
from agent_worktrees import state_root as sr

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

    monkeypatch.setattr(cfg, "load_config", lambda: _stateless_config(harness))
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
