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


def test_env_override_wins(tmp_path, monkeypatch) -> None:
    _registry(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_INDEX_SOURCES", "git:only")
    specs = engine.configured_source_specs()
    assert [s.name for s in specs] == ["git:only"]


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
