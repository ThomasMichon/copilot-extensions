"""Federated corpus sweep: graft corpus.sources from adopted local projects."""

from __future__ import annotations

import textwrap

from agent_index import config as cfg
from agent_index.indexing import engine


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _registry(tmp_path, monkeypatch):
    """Lay down an agent-worktrees registry (projects.yaml + repos.yaml) and two
    repo checkouts, and point config at them."""
    aw = tmp_path / ".agent-worktrees"
    dotfiles = tmp_path / "src" / "dotfiles"
    ce = tmp_path / "src" / "copilot-extensions"
    (dotfiles / ".agent-index").mkdir(parents=True)
    (ce / ".agent-index").mkdir(parents=True)

    _write(aw / "projects.yaml", """\
        schema_version: 2
        projects:
          dotfiles: {config_dir: "~/.dotfiles"}
          copilot-extensions: {config_dir: "~/.copilot-extensions"}
          unregistered-path: {config_dir: "~/.x"}
    """)
    _write(aw / "repos.yaml", f"""\
        schema_version: 1
        repos:
          dotfiles: {{class: worktree, windows: "{dotfiles.as_posix()}"}}
          copilot-extensions: {{class: worktree, windows: "{ce.as_posix()}"}}
    """)
    # dotfiles centrally declares itself + copilot-extensions + a github source.
    _write(dotfiles / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: dotfiles
              trust_domain: work
            - name: git:copilot-extensions
              repo: copilot-extensions
              trust_domain: personal
            - name: github:owner/dotfiles
              type: github
              repo: owner/dotfiles
              auth: {account: someacct}
    """)
    # copilot-extensions self-declares nothing new (dedup: dotfiles wins).
    _write(ce / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:copilot-extensions
              repo: copilot-extensions
    """)
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(aw))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))  # empty machine-local
    monkeypatch.setattr(cfg, "_registry_platform_key", lambda: "windows")
    return dotfiles, ce


def test_sweep_grafts_project_sources(tmp_path, monkeypatch) -> None:
    _registry(tmp_path, monkeypatch)
    sources = cfg.read_corpus_sources()
    names = [s["name"] for s in sources]
    # All three declared by dotfiles are present; dedup keeps one copilot-extensions.
    assert names.count("git:copilot-extensions") == 1
    assert set(names) == {"git:dotfiles", "git:copilot-extensions", "github:owner/dotfiles"}


def test_specs_resolve_correct_per_repo_paths(tmp_path, monkeypatch) -> None:
    dotfiles, ce = _registry(tmp_path, monkeypatch)
    specs = {s.name: s for s in engine.configured_source_specs()}

    # A git source's checkout path resolves to ITS OWN repo (explicit repo: wins
    # over the declaring project's path) — not the declaring dotfiles path.
    assert engine._connector_kwargs(specs["git:dotfiles"]) == {"repo_path": str(dotfiles)}
    assert engine._connector_kwargs(specs["git:copilot-extensions"]) == {"repo_path": str(ce)}

    # A github source resolves a token via the account (mock gh).
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda account: f"tok-{account}")
    assert engine._connector_kwargs(specs["github:owner/dotfiles"]) == {"token": "tok-someacct"}


def test_explicit_commits_source_resolves_against_parent_repo(tmp_path, monkeypatch) -> None:
    """A direct ``--source git:X:commits`` request (as a per-source full-reindex
    task, not the ``source=None``/"all" sweep that naturally bundles commits
    as a ``discover()`` byproduct of its parent) must resolve the SAME repo
    path/ref/auth as its parent ``git:X`` file source -- not synthesize a bare,
    repo-less spec that silently falls back to the wrong cwd (#1350)."""
    dotfiles, ce = _registry(tmp_path, monkeypatch)
    _write(dotfiles / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: dotfiles
            - name: git:copilot-extensions
              repo: copilot-extensions
              ref: origin/dev
              auth: {account: someacct}
    """)
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda account: f"tok-{account}")
    by_name = {s.name: s for s in engine.configured_source_specs()}

    spec = engine._resolve_explicit_source_spec("git:copilot-extensions:commits", by_name)
    assert spec.name == "git:copilot-extensions:commits"
    assert engine._connector_kwargs(spec) == {
        "repo_path": str(ce),
        "ref": "origin/dev",
        "token": "tok-someacct",
    }


def test_unconfigured_commits_source_without_parent_raises(tmp_path, monkeypatch) -> None:
    """A ``:commits`` request whose base name ISN'T a configured source either
    (typo, stale name) still can't silently resolve to a bogus cwd default --
    it must raise, same as any other unresolvable explicit source name."""
    _registry(tmp_path, monkeypatch)
    by_name = {s.name: s for s in engine.configured_source_specs()}

    spec = engine._resolve_explicit_source_spec("git:nonexistent:commits", by_name)
    assert spec.repo is None and spec.repo_path is None
    try:
        engine._connector_kwargs(spec)
        raise AssertionError("expected RuntimeError for an unresolvable named source")
    except RuntimeError as exc:
        assert "git:nonexistent:commits" in str(exc)


def test_unconfigured_named_source_raises_not_silent_cwd_fallback(tmp_path, monkeypatch) -> None:
    """Any OTHER explicitly-named (non-``:commits``, non-bare-``git``) source
    that fails to resolve a checkout path must also raise loudly -- not just
    the narrower repo/repo_path-hinted case #1350 originally covered."""
    _registry(tmp_path, monkeypatch)
    by_name = {s.name: s for s in engine.configured_source_specs()}

    spec = engine._resolve_explicit_source_spec("git:totally-unknown", by_name)
    try:
        engine._connector_kwargs(spec)
        raise AssertionError("expected RuntimeError for an unresolvable named source")
    except RuntimeError as exc:
        assert "git:totally-unknown" in str(exc)


def test_git_source_ref_override_and_authenticated_fetch(tmp_path, monkeypatch) -> None:
    """A ``git:`` source may declare ``ref:`` (index a branch other than the
    remote's default, e.g. a repo whose integration branch is ``dev`` not
    ``main``) and ``auth.account`` (authenticate its fetch step the same way a
    ``github:`` source's token is resolved) -- both wired through to the
    connector kwargs, independent of each other and of other sources."""
    dotfiles, ce = _registry(tmp_path, monkeypatch)
    _write(dotfiles / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: dotfiles
              trust_domain: work
            - name: git:copilot-extensions
              repo: copilot-extensions
              ref: origin/dev
              auth: {account: someacct}
              trust_domain: personal
            - name: github:owner/dotfiles
              type: github
              repo: owner/dotfiles
              auth: {account: someacct}
    """)
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda account: f"tok-{account}")
    specs = {s.name: s for s in engine.configured_source_specs()}

    assert engine._connector_kwargs(specs["git:dotfiles"]) == {"repo_path": str(dotfiles)}
    assert engine._connector_kwargs(specs["git:copilot-extensions"]) == {
        "repo_path": str(ce),
        "ref": "origin/dev",
        "token": "tok-someacct",
    }


def test_git_source_ref_override_does_not_require_auth(tmp_path, monkeypatch) -> None:
    """``ref:`` alone (no ``auth:``) does not force a token resolution -- a
    repo may need a non-default branch without needing authenticated fetch."""
    dotfiles, ce = _registry(tmp_path, monkeypatch)
    _write(dotfiles / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: dotfiles
            - name: git:copilot-extensions
              repo: copilot-extensions
              ref: origin/dev
    """)

    def _unexpected_resolve(account):
        raise AssertionError("must not resolve a token when auth.account is unset")

    monkeypatch.setattr(engine, "_resolve_gh_token", _unexpected_resolve)
    specs = {s.name: s for s in engine.configured_source_specs()}
    assert engine._connector_kwargs(specs["git:copilot-extensions"]) == {
        "repo_path": str(ce),
        "ref": "origin/dev",
    }


def test_git_source_auth_token_resolution_failure_is_non_fatal(tmp_path, monkeypatch) -> None:
    """Unlike a ``github:`` source (hard failure), a ``git:`` source whose
    token can't be resolved proceeds WITHOUT one -- GitRepoConnector's own
    fetch already falls back gracefully to stale-but-canonical remote-tracking
    state or the local HEAD, so there's no need to fail the whole source."""
    dotfiles, ce = _registry(tmp_path, monkeypatch)
    _write(dotfiles / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: dotfiles
            - name: git:copilot-extensions
              repo: copilot-extensions
              auth: {account: someacct}
    """)
    monkeypatch.setattr(engine, "_resolve_gh_token", lambda account: None)
    specs = {s.name: s for s in engine.configured_source_specs()}
    assert engine._connector_kwargs(specs["git:copilot-extensions"]) == {"repo_path": str(ce)}


def test_env_override_wins(tmp_path, monkeypatch) -> None:
    _registry(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_INDEX_SOURCES", "git:only")
    specs = engine.configured_source_specs()
    assert [s.name for s in specs] == ["git:only"]


def test_current_repo_layers_union_sources_and_preserve_overlay_indexer(
    tmp_path, monkeypatch
) -> None:
    aw = tmp_path / ".agent-worktrees"
    _write(aw / "projects.yaml", "schema_version: 2\nprojects: {}\n")
    _write(aw / "repos.yaml", "schema_version: 1\nrepos: {}\n")
    root = tmp_path / "harness"
    _write(root / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: github:example-org/example-repo
              trust_domain: harness
    """)
    _write(root / ".copilot-extensions" / "agent-index" / "config.yaml", """\
        indexer:
          machine: boxA
        corpus:
          sources:
            - name: github:ThomasMichon/copilot-extensions
              trust_domain: marketplace
    """)
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(aw))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cfg, "repo_root", lambda explicit=None: root)

    assert cfg.read_indexer(root) == {"machine": "boxA"}
    sources = cfg.read_corpus_sources()
    assert [s["name"] for s in sources] == [
        "github:ThomasMichon/copilot-extensions",
        "github:example-org/example-repo",
    ]


def test_current_repo_duplicate_source_name_uses_higher_precedence_overlay(
    tmp_path, monkeypatch
) -> None:
    aw = tmp_path / ".agent-worktrees"
    _write(aw / "projects.yaml", "schema_version: 2\nprojects: {}\n")
    _write(aw / "repos.yaml", "schema_version: 1\nrepos: {}\n")
    root = tmp_path / "harness"
    _write(root / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: dotfiles
              trust_domain: shareable
    """)
    _write(root / ".copilot-extensions" / "agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: git:dotfiles
              repo: override
              trust_domain: overlay
    """)
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(aw))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cfg, "repo_root", lambda explicit=None: root)

    sources = cfg.read_corpus_sources()
    assert sources == [
        {
            "name": "git:dotfiles",
            "repo": "override",
            "trust_domain": "overlay",
            "_repo_path": str(root),
            "_contributed_by": "effective-config",
        }
    ]


def test_current_repo_unions_base_knowledge_and_machine_local_sources(
    tmp_path, monkeypatch
) -> None:
    aw = tmp_path / ".agent-worktrees"
    _write(aw / "projects.yaml", "schema_version: 2\nprojects: {}\n")
    _write(aw / "repos.yaml", "schema_version: 1\nrepos: {}\n")
    root = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    _write(root / ".agent-worktrees" / "config.yaml", """\
        requires_external_state_root: true
    """)
    _write(root / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: github:example-org/example-repo
              trust_domain: harness
    """)
    _write(knowledge / ".agent-index" / "config.yaml", """\
        corpus:
          sources:
            - name: github:owner/dotfiles
              trust_domain: knowledge
    """)
    _write(tmp_path / "home" / "config.yaml", """\
        corpus:
          sources:
            - name: github:personal/notes
              trust_domain: machine
    """)
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(aw))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cfg, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(cfg, "_external_state_root", lambda _root: ("ready", knowledge))

    sources = cfg.read_corpus_sources()
    assert [s["name"] for s in sources] == [
        "github:owner/dotfiles",
        "github:example-org/example-repo",
        "github:personal/notes",
    ]


def test_default_when_no_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(tmp_path / "nope"))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENT_INDEX_SOURCES", raising=False)
    monkeypatch.setattr(cfg, "repo_root", lambda explicit=None: None)
    specs = engine.configured_source_specs()
    assert [s.name for s in specs] == ["git"]
    # The bare default 'git' source must resolve to NO connector kwargs (the
    # connector falls back to cwd) — not raise for an unresolvable registry path.
    assert engine._connector_kwargs(specs[0]) == {}
