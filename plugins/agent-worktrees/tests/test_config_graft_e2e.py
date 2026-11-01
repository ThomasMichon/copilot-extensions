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
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import disposition_history as dh
from agent_worktrees import related, tracking
from agent_worktrees import state_root as sr


@pytest.mark.parametrize("exit_code", [1, 3, 126])
def test_native_optional_configuration_owner_refusal_is_not_absence(tmp_path, exit_code):
    from configuration_context import read_owner_context
    from configuration_layers import ConfigurationError

    with pytest.raises(ConfigurationError, match="refused"):
        read_owner_context(
            [sys.executable, "-c", f"import sys; sys.exit({exit_code})"],
            tmp_path, required=False,
        )


def test_native_optional_configuration_owner_unsupported_option(tmp_path):
    from configuration_context import read_owner_context

    prefix = [
        sys.executable, "-c",
        "import sys; print('unrecognized arguments: --configuration', "
        "file=sys.stderr); sys.exit(2)",
    ]
    assert read_owner_context(prefix, tmp_path) is None


def test_configuration_owner_same_named_remote_and_cell_separation(tmp_path, monkeypatch):
    from configuration_context import context_from_owner
    from configuration_layers import ConfigurationError

    from agent_worktrees import config_sources, registry_paths, repos, state_root

    anchor = tmp_path / "shared"
    anchor.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path), machine="logical-machine", platform="windows",
        repo_name="shared", repos={"shared": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "trees"),
        )},
    )
    records = []
    for owner, cell in (("one", "cell-one"), ("two", "cell-one"), ("one", "cell-two")):
        entry = repos.RepoEntry(
            name="shared", remote=f"https://example.test/{owner}/shared.git",
            paths={"windows": str(anchor)},
        )
        monkeypatch.setattr(
            repos, "read_registry",
            lambda entry=entry, **kwargs: repos.ReposRegistry(repos={"shared": entry}),
        )
        monkeypatch.setattr(registry_paths, "installation_context", lambda cell=cell: {
            "cellRoot": str(tmp_path / cell), "marketplaceId": "market",
        })
        monkeypatch.setattr(
            cfg, "project_dir", lambda project=None, cell=cell: tmp_path / cell / "project",
        )
        report = config_sources.configuration_report(config)
        records.append(context_from_owner(report))
    assert records[0]["repository_id"] != records[1]["repository_id"]
    assert records[0]["repository_id"] == records[2]["repository_id"]
    assert records[0]["installation_cell"] != records[2]["installation_cell"]
    config = cfg.Config(
        srcroot=str(tmp_path), machine="logical-machine", platform="windows",
        repo_name="shared", repos=config.repos, knowledge_repo="missing",
    )
    monkeypatch.setattr(state_root, "_checkout_path", lambda name: None)
    with pytest.raises(ConfigurationError, match="binding is unresolved"):
        config_sources.configuration_report(config)

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


def test_native_launcher_reserves_and_delivers_configuration_data(split, monkeypatch):
    from configuration_context import delivered_context

    from agent_worktrees import launch_environment, repos

    harness, knowledge = split
    registry = repos.ReposRegistry(repos={
        "harness": repos.RepoEntry(
            name="harness", remote="https://example.test/base/harness.git",
        ),
    })
    monkeypatch.setattr(repos, "read_registry", lambda **kwargs: registry)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "harness")
    monkeypatch.setattr(launch_environment, "_core", lambda: SimpleNamespace(
        _self_owner_ref=lambda *args, **kwargs: None,
    ))
    profile = SimpleNamespace(env={"AGENT_CONFIGURATION_CONTEXT": "forged-profile"})

    environment = launch_environment._build_env(
        profile, {"AGENT_CONFIGURATION_CONTEXT": "forged-repository"},
        work_dir=str(harness), config=cfg.load_config(),
    )
    report = delivered_context(environment, repository_root=harness)

    assert report["context"]["binding"] == "bound"
    assert report["context"]["knowledge_root"] == str(knowledge)
    assert report["context"]["execution_machine"] == "test"


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


def test_related_resolve_reminder_uses_remote_slug_not_alias(
    split, capfd, monkeypatch
):
    """The account-routing reminder must use the real gh owner/name slug
    parsed from the remote, not the registry alias -- a registry name (e.g.
    'example-web') can differ from the repo it actually points at."""
    from agent_worktrees import repos
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    monkeypatch.setattr(
        repos, "find_repo",
        lambda name: repos.RepoEntry(
            name=name, remote="https://github.com/example-org/proj.git",
        ) if name == "example-web" else None,
    )
    assert run(["resolve", "example-web", "--repo", str(harness), "--json"]) == 0
    out = json.loads(capfd.readouterr().out)
    assert out["account"] == "example-org"
    assert "example-org/proj" in out["account_routing_reminder"]
    assert "example-web" not in out["account_routing_reminder"]


def test_related_resolve_reminder_human_output_uses_remote_slug(
    split, capfd, monkeypatch
):
    from agent_worktrees import repos
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    monkeypatch.setattr(
        repos, "find_repo",
        lambda name: repos.RepoEntry(
            name=name, remote="https://github.com/example-org/proj.git",
        ) if name == "example-web" else None,
    )
    assert run(["resolve", "example-web", "--repo", str(harness)]) == 0
    out = capfd.readouterr().out
    assert "repos gh example-org/proj --" in out
    assert "repos gh example-web --" not in out


def test_related_resolve_reminder_falls_back_without_parseable_remote(
    split, capfd, monkeypatch
):
    """When no gh owner/name slug can be derived (e.g. no remote, or a
    non-GitHub remote) but an account is still resolved (explicit override),
    the reminder must not guess the registry alias as the slug -- it should
    degrade to generic wording that says so."""
    from agent_worktrees import repos
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    monkeypatch.setattr(
        repos, "find_repo",
        lambda name: repos.RepoEntry(name=name, account="someone", remote="")
        if name == "example-web" else None,
    )
    assert run(["resolve", "example-web", "--repo", str(harness), "--json"]) == 0
    out = json.loads(capfd.readouterr().out)
    assert out["account"] == "someone"
    assert "owner/name" in out["account_routing_reminder"]
    assert "'example-web' may not match it" in out["account_routing_reminder"]


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


# ---------------------------------------------------------------------------
# The machine-local project root (`get config-dir`) is an overlay source too:
# harness setup writes machine-specific related entries there (#508).
# ---------------------------------------------------------------------------

def _machine_root(tmp_path, monkeypatch, related_yaml):
    root = tmp_path / "machine-root"
    (root / ".agent-worktrees").mkdir(parents=True)
    (root / ".agent-worktrees" / "related.yaml").write_text(related_yaml, encoding="utf-8")
    monkeypatch.setattr(cfg, "project_dir", lambda *a, **k: root)
    return root


_MACHINE_RELATED = """\
related:
  machine-only-web:
    role: product
    summary: "Written by harness setup on this machine."
    locus: { preferred: codespace }
    delegate: { via: agent-codespaces }
"""


def test_config_sources_include_the_machine_local_root_after_knowledge(
    split, tmp_path, monkeypatch,
):
    harness, knowledge = split
    root = _machine_root(tmp_path, monkeypatch, _MACHINE_RELATED)
    srcs = sr.config_source_anchors(cfg.load_config(), base_anchor=str(harness))
    assert [(s.origin, s.anchor) for s in srcs] == [
        ("harness", str(harness)),
        ("knowledge", str(knowledge)),
        ("machine", str(root)),
    ]


def test_related_resolve_from_a_harness_checkout_sees_machine_local_entries(split, tmp_path, monkeypatch, capfd):
    from agent_worktrees.__main__ import cmd_related_dispatch as run
    harness, _ = split
    _machine_root(tmp_path, monkeypatch, _MACHINE_RELATED)
    assert run(["resolve", "machine-only-web", "--repo", str(harness)]) == 0
    assert "Written by harness setup" in capfd.readouterr().out


def test_machine_local_root_without_agent_config_is_not_a_source(split, tmp_path, monkeypatch):
    harness, knowledge = split
    empty = tmp_path / "empty-root"
    empty.mkdir()
    monkeypatch.setattr(cfg, "project_dir", lambda *a, **k: empty)
    srcs = sr.config_source_anchors(cfg.load_config(), base_anchor=str(harness))
    assert [s.origin for s in srcs] == ["harness", "knowledge"]


@pytest.fixture
def ordinary_related_sources(tmp_path, monkeypatch):
    from agent_worktrees import repos

    roots = {name: tmp_path / name / "shared" for name in ("base", "foreign")}
    knowledge = {name: tmp_path / f"knowledge-{name}" for name in roots}
    local = {name: tmp_path / "cell-a" / name for name in roots}
    global_root = tmp_path / "cell-a" / "machine"
    for root in [*roots.values(), *knowledge.values(), *local.values(), global_root]:
        root.mkdir(parents=True)
    configs = {
        name: cfg.Config(
            srcroot=str(tmp_path), machine="execution-a", platform="windows",
            repo_name=name, knowledge_repo=f"knowledge-{name}",
            repos={name: cfg.RepoConfig(anchor=str(root), worktree_root=str(root) + ".trees")},
        )
        for name, root in roots.items()
    }
    registered_roots = {
        **roots, **{f"knowledge-{name}": root for name, root in knowledge.items()},
    }
    entries = {
        name: repos.RepoEntry(
            name=name, repo_class="worktree", remote=f"https://example.test/{name}/shared.git",
            paths={"windows": str(root)}, default_branch="main",
        )
        for name, root in registered_roots.items()
    }
    monkeypatch.setattr(
        repos, "read_registry", lambda **kwargs: repos.ReposRegistry(repos=entries),
    )
    monkeypatch.setattr(
        cfg, "load_config", lambda *args, **kwargs: configs[kwargs.get("project") or "base"],
    )
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: local[project or "base"])
    monkeypatch.setattr(cfg, "install_dir", lambda: global_root)
    monkeypatch.setattr(related, "installed_plugin_related_anchors", lambda: [])
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    return roots, knowledge, local, global_root, configs


def _write_related_source(root, summary, *, direct=False):
    path = root / "related.yaml" if direct else related.legacy_related_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "primary: target\nrelated:\n  target:\n"
        f"    summary: {summary}\n    doc: narratives/target.md\n", encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("level", range(4))
def test_related_cli_stateful_binding_four_tiers_and_document_owner(
    ordinary_related_sources, level, capfd,
):
    from agent_worktrees import related_cli

    roots, knowledge, local, global_root, configs = ordinary_related_sources
    paths = [_write_related_source(roots["base"], "base")]
    if level >= 1:
        paths.append(_write_related_source(knowledge["base"], "knowledge"))
    if level >= 2:
        paths.append(_write_related_source(global_root, "machine", direct=True))
    if level >= 3:
        paths.append(_write_related_source(local["base"], "exact"))

    assert related_cli.cmd_related_dispatch(["list", "--repo", str(roots["base"]), "--json"]) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["related"][0]["summary"] == ("base", "knowledge", "machine", "exact")[level]
    anchors = related_cli._related_config_source_anchors(str(roots["base"]))
    entry = related.read_related_grafted(anchors).related["target"]
    expected_document = paths[-1].parent / "narratives" / "target.md"
    assert related.doc_abs_path(roots["base"], entry) == expected_document
    assert configs["base"].default_repo.stateless is False
    if level == 3:
        assert [anchor.source_tier for anchor in anchors] == [
            "repository", "repository", "machine", "repository_machine",
        ]


def test_related_explicit_foreign_same_basename_uses_its_own_graft(
    ordinary_related_sources, capfd,
):
    from agent_worktrees import related_cli

    roots, knowledge, local, _, _ = ordinary_related_sources
    _write_related_source(knowledge["base"], "wrong-launch-graft")
    _write_related_source(knowledge["foreign"], "foreign-graft")
    _write_related_source(local["base"], "wrong-launch-machine")
    _write_related_source(local["foreign"], "foreign-machine")
    argv = ["list", "--repo", str(roots["foreign"]), "--json"]
    assert related_cli.cmd_related_dispatch(argv) == 0
    assert json.loads(capfd.readouterr().out)["related"][0]["summary"] == "foreign-machine"
    anchors = related_cli._related_config_source_anchors(str(roots["foreign"]))
    assert str(knowledge["base"]) not in [str(anchor) for anchor in anchors]
    assert str(local["base"]) not in [str(anchor) for anchor in anchors]
    assert related.read_related_grafted(anchors).related["target"].summary == "foreign-machine"


def test_related_unregistered_explicit_target_does_not_borrow_binding(
    ordinary_related_sources, tmp_path,
):
    from agent_worktrees import related_cli

    _, knowledge, local, _, _ = ordinary_related_sources
    target = tmp_path / "unregistered" / "shared"
    _write_related_source(target, "standalone")
    _write_related_source(knowledge["base"], "wrong-graft")
    _write_related_source(local["base"], "wrong-exact")
    anchors = related_cli._related_config_source_anchors(str(target))
    assert [str(anchor) for anchor in anchors] == [str(target)]
    assert related.read_related_grafted(anchors).related["target"].summary == "standalone"


def test_related_explicit_binding_refusal_never_falls_back_to_base(ordinary_related_sources):
    from configuration_layers import ConfigurationError

    from agent_worktrees import related_cli

    roots, _, _, _, configs = ordinary_related_sources
    _write_related_source(roots["base"], "must-not-be-returned")
    configs["base"] = cfg.Config(
        srcroot="", machine="execution-a", platform="windows", repo_name="base",
        repos=configs["base"].repos, knowledge_repo="missing-registration",
    )
    with pytest.raises(ConfigurationError, match="binding is unresolved"):
        related_cli.cmd_related_dispatch(["list", "--repo", str(roots["base"]), "--json"])


@pytest.mark.parametrize("machine,cell", [
    ("execution-a", "cell-a"), ("execution-b", "cell-a"), ("execution-a", "cell-b"),
])
def test_related_cli_machine_and_cell_roots_are_independent(
    ordinary_related_sources, tmp_path, monkeypatch, capfd, machine, cell,
):
    from dataclasses import replace

    from agent_worktrees import related_cli

    roots, _, _, _, configs = ordinary_related_sources
    configs["base"] = replace(configs["base"], machine=machine)
    global_root = tmp_path / "selected" / cell / machine / "global"
    local = tmp_path / "selected" / cell / machine / "exact"
    _write_related_source(global_root, "generic", direct=True)
    _write_related_source(local, f"{cell}-{machine}")
    other = tmp_path / "unselected-cell" / machine
    _write_related_source(other, "must-not-leak")
    monkeypatch.setattr(cfg, "install_dir", lambda: global_root)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: local)
    assert related_cli.cmd_related_dispatch(["list", "--repo", str(roots["base"]), "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["related"][0]["summary"] == f"{cell}-{machine}"


def test_related_cli_rejects_refused_installation_root(ordinary_related_sources, monkeypatch):
    from configuration_layers import ConfigurationError

    from agent_worktrees import related_cli

    roots, _, _, _, _ = ordinary_related_sources

    def refused():
        raise ValueError("native installation owner refused this cell")

    monkeypatch.setattr(cfg, "install_dir", refused)
    with pytest.raises(ConfigurationError, match="machine-wide configuration root"):
        related_cli.cmd_related_dispatch(["list", "--repo", str(roots["base"]), "--json"])


def test_related_cli_rejects_refused_namespaced_exact_root(
    ordinary_related_sources, monkeypatch,
):
    from configuration_layers import ConfigurationError

    from agent_worktrees import related_cli

    roots, _, _, _, _ = ordinary_related_sources

    def refused(project=None):
        raise ValueError("native repository identity receipt refused")

    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{}")
    monkeypatch.setattr(cfg, "project_dir", refused)
    with pytest.raises(ConfigurationError, match="selected repository configuration root"):
        related_cli.cmd_related_dispatch(["list", "--repo", str(roots["base"]), "--json"])


def test_related_cli_unbound_keeps_base_and_write_policy_separate(
    ordinary_related_sources, monkeypatch, capfd,
):
    from dataclasses import replace

    from agent_worktrees import related_cli

    roots, knowledge, _, _, configs = ordinary_related_sources
    configs["base"] = replace(configs["base"], knowledge_repo="")
    _write_related_source(roots["base"], "unbound-base")
    _write_related_source(knowledge["base"], "must-not-graft")
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd=None: str(roots["base"]))
    assert related_cli.cmd_related_dispatch(["list", "--repo", str(roots["base"]), "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["related"][0]["summary"] == "unbound-base"
    assert sr.resolve_state_root(configs["base"]).source == "launch_repo"
    assert sr.resolve_state_root(configs["base"]).requires_external is False


def test_related_binding_read_report_retains_refusal_not_absence(ordinary_related_sources):
    from dataclasses import replace

    from dropin_registry import ScanAuthority

    from agent_worktrees import config_sources

    roots, _, _, _, configs = ordinary_related_sources
    config = replace(configs["base"], knowledge_repo="unregistered-binding")
    report = config_sources.config_source_anchors_report(config, base_anchor=str(roots["base"]))
    assert report.authority is ScanAuthority.INDETERMINATE
    assert any(finding.reason == "knowledge-unavailable" for finding in report.findings)


def test_machine_local_root_is_not_duplicated_when_it_is_the_base(split, tmp_path, monkeypatch):
    root = _machine_root(tmp_path, monkeypatch, _MACHINE_RELATED)
    srcs = sr.config_source_anchors(cfg.load_config(), base_anchor=str(root))
    assert [s.origin for s in srcs].count("machine") == 0
