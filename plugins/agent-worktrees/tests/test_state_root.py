"""Tests for agent_worktrees.state_root -- the stateless-harness state-root resolver."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import state_root as sr


def _config(repo_name, *, stateless=False, requires_external_state_root=False,
            knowledge_repo="", anchor="/anchor"):
    return cfg.Config(
        srcroot="/src",
        machine="test",
        platform="linux",
        repo_name=repo_name,
        knowledge_repo=knowledge_repo,
        repos={
            repo_name: cfg.RepoConfig(
                anchor=anchor,
                worktree_root=f"{anchor}.worktrees",
                default_branch="main",
                remote="origin",
                stateless=stateless,
                requires_external_state_root=requires_external_state_root,
            )
        },
    )


@pytest.fixture
def fake_checkouts(monkeypatch):
    """Patch the registry->checkout resolver with an in-memory name->path map."""
    table: dict[str, str] = {}

    def _resolve(name):
        return table.get(name)

    monkeypatch.setattr(sr, "_checkout_path", _resolve)
    return table


# ---------------------------------------------------------------------------
# Non-stateless (backward compatible): the launch repo is the state home.
# ---------------------------------------------------------------------------

def test_non_stateless_uses_git_toplevel(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: "/work/tree")
    res = sr.resolve_state_root(_config("dotfiles"))
    assert res.path == "/work/tree"
    assert res.source == "launch_repo"
    assert res.stateless is False
    assert res.requires_external is False
    assert res.bound is True
    assert res.error is None


def test_non_stateless_falls_back_to_anchor(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: None)
    res = sr.resolve_state_root(_config("dotfiles", anchor=str(tmp_path)))
    assert res.path == str(tmp_path)
    assert res.source == "launch_repo"
    assert res.bound is True


def test_non_stateless_unresolvable(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: None)
    res = sr.resolve_state_root(_config("dotfiles", anchor="/does/not/exist"))
    assert res.path is None
    assert res.bound is False
    assert "could not resolve" in res.error


# ---------------------------------------------------------------------------
# Stateless harness -> the bound knowledge repo (no fallback).
# ---------------------------------------------------------------------------

def test_stateless_bound_resolves_knowledge_repo(fake_checkouts):
    fake_checkouts["citadel-knowledge"] = "/repos/knowledge"
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="citadel-knowledge")
    )
    assert res.path == "/repos/knowledge"
    assert res.source == "knowledge_repo"
    assert res.repo == "citadel-knowledge"
    assert res.stateless is True
    assert res.requires_external is True  # stateless implies it
    assert res.bound is True


def test_requires_external_without_stateless_routes_knowledge(fake_checkouts):
    # A repo can require an external state root without being a stateless harness.
    fake_checkouts["knowledge"] = "/repos/k"
    res = sr.resolve_state_root(
        _config("some-repo", requires_external_state_root=True, knowledge_repo="knowledge")
    )
    assert res.path == "/repos/k"
    assert res.source == "knowledge_repo"
    assert res.stateless is False
    assert res.requires_external is True
    assert res.bound is True


def test_requires_external_unbound_refuses(fake_checkouts):
    res = sr.resolve_state_root(
        _config("some-repo", requires_external_state_root=True, knowledge_repo="")
    )
    assert res.path is None
    assert res.bound is False
    assert res.stateless is False
    assert res.requires_external is True
    assert "requires an external state root" in res.error


def test_stateless_unbound_refuses(fake_checkouts):
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="")
    )
    assert res.path is None
    assert res.bound is False
    assert res.stateless is True
    assert res.requires_external is True
    assert "no knowledge_repo is bound" in res.error
    # Must NOT fall back to the launch repo tree.
    assert "citadel-harness" in res.error


def test_stateless_bound_but_unregistered(fake_checkouts):
    # knowledge_repo points at a name with no registered checkout.
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="ghost")
    )
    assert res.path is None
    assert res.bound is False
    assert "not a registered repo" in res.error


# ---------------------------------------------------------------------------
# Explicit override wins over the binding.
# ---------------------------------------------------------------------------

def test_explicit_override_targets_named_repo(fake_checkouts):
    fake_checkouts["example-web"] = "/repos/example-web"
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="citadel-knowledge"),
        repo_override="example-web",
    )
    assert res.path == "/repos/example-web"
    assert res.source == "explicit"
    assert res.repo == "example-web"


def test_explicit_override_unregistered(fake_checkouts):
    res = sr.resolve_state_root(
        _config("dotfiles"), repo_override="nope"
    )
    assert res.path is None
    assert res.source == "explicit"
    assert "not a registered repo" in res.error


def test_config_root_defaults_to_machine_local_project_dir(monkeypatch, tmp_path):
    machine_root = tmp_path / ".harness"
    monkeypatch.setattr(cfg, "project_dir", lambda _name=None: machine_root)

    res = sr.resolve_config_root(
        _config("harness", stateless=True, anchor=str(tmp_path / "harness"))
    )

    assert res.path == str(machine_root.resolve())
    assert res.source == "machine_local"
    assert res.repo == "harness"
    assert res.stateless is True
    assert res.bound is True
    assert res.error is None


def test_config_root_rejects_explicit_stateless_checkout(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()

    res = sr.resolve_config_root(
        _config("harness", stateless=True, anchor=str(harness)),
        destination=str(harness / "generated"),
        cwd=str(harness),
    )

    assert res.path is None
    assert res.source == "explicit"
    assert res.bound is False
    assert "inside stateless checkout" in res.error
    assert "agent-worktrees config-root" in res.error


def test_config_root_rejects_other_declared_stateless_checkout(tmp_path):
    launch = tmp_path / "launch"
    launch.mkdir()
    target = tmp_path / "other-harness"
    (target / ".git").mkdir(parents=True)
    (target / ".agent-worktrees").mkdir()
    (target / ".agent-worktrees" / "config.yaml").write_text(
        "requires_external_state_root: true\n",
        encoding="utf-8",
    )

    res = sr.resolve_config_root(
        _config("launch", anchor=str(launch)),
        destination=str(target / "generated"),
        cwd=str(launch),
    )

    assert res.path is None
    assert res.bound is False
    assert res.stateless is False
    assert str(target) in res.error


def test_config_root_rejects_sibling_worktree_from_machine_local_stateless_config(
    tmp_path,
):
    anchor = tmp_path / "harness"
    sibling = tmp_path / "harness-worktree"
    anchor.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=anchor, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=anchor,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=anchor,
        check=True,
    )
    (anchor / "README.md").write_text("harness\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=anchor, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=anchor, check=True)
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "sibling", str(sibling)],
        cwd=anchor,
        check=True,
    )

    res = sr.resolve_config_root(
        _config("harness", stateless=True, anchor=str(anchor)),
        destination=str(sibling / "generated"),
        cwd=str(anchor),
    )

    assert res.path is None
    assert res.bound is False
    assert str(sibling) in res.error


@pytest.mark.parametrize("nested_kind", ["repository", "gitfile"])
def test_config_root_rejects_nested_checkout_inside_stateless_sibling(
    tmp_path,
    nested_kind,
):
    anchor = tmp_path / "harness"
    sibling = tmp_path / "harness-worktree"
    nested = sibling / "nested"
    anchor.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=anchor, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=anchor,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=anchor,
        check=True,
    )
    (anchor / "README.md").write_text("harness\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=anchor, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=anchor, check=True)
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "sibling", str(sibling)],
        cwd=anchor,
        check=True,
    )
    nested.mkdir()
    if nested_kind == "repository":
        subprocess.run(["git", "init", "--quiet"], cwd=nested, check=True)
    else:
        git_dir = anchor / ".git" / "modules" / "nested"
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "init",
                "--quiet",
                "--separate-git-dir",
                str(git_dir),
                str(nested),
            ],
            check=True,
        )
        assert (nested / ".git").is_file()

    res = sr.resolve_config_root(
        _config("harness", stateless=True, anchor=str(anchor)),
        destination=str(nested / "generated"),
        cwd=str(anchor),
    )

    assert res.path is None
    assert res.bound is False
    assert res.stateless is True
    assert str(sibling) in res.error


def test_validate_config_destination_rejection_has_no_launch_statelessness(tmp_path):
    launch = tmp_path / "launch"
    target = tmp_path / "target"
    (launch / ".git").mkdir(parents=True)
    (target / ".git").mkdir(parents=True)
    (target / ".agent-worktrees").mkdir()
    (target / ".agent-worktrees" / "config.yaml").write_text(
        "stateless: true\n",
        encoding="utf-8",
    )

    res = sr.validate_config_destination(
        str(target / "generated"),
        cwd=str(launch),
    )

    assert res.path is None
    assert res.bound is False
    assert res.stateless is False
    assert res.repo == ""


def test_validate_config_destination_preserves_launch_statelessness(tmp_path):
    launch = tmp_path / "launch"
    target = tmp_path / "target"
    (launch / ".git").mkdir(parents=True)
    (launch / ".agent-worktrees").mkdir()
    (launch / ".agent-worktrees" / "config.yaml").write_text(
        "stateless: true\n",
        encoding="utf-8",
    )
    (target / ".git").mkdir(parents=True)
    (target / ".agent-worktrees").mkdir()
    (target / ".agent-worktrees" / "config.yaml").write_text(
        "stateless: true\n",
        encoding="utf-8",
    )

    res = sr.validate_config_destination(
        str(target / "generated"),
        cwd=str(launch),
    )

    assert res.path is None
    assert res.bound is False
    assert res.stateless is True


@pytest.mark.parametrize("nested_kind", [None, "repository", "gitfile"])
def test_unadopted_stateless_launch_rejects_stale_sibling_checkout(
    tmp_path,
    nested_kind,
):
    launch = tmp_path / "harness"
    sibling = tmp_path / "stale-worktree"
    launch.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=launch, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=launch,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=launch,
        check=True,
    )
    (launch / "README.md").write_text("harness\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=launch, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=launch, check=True)
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "stale", str(sibling)],
        cwd=launch,
        check=True,
    )
    (launch / ".agent-worktrees").mkdir()
    (launch / ".agent-worktrees" / "config.yaml").write_text(
        "stateless: true\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".agent-worktrees/config.yaml"], cwd=launch, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "declare stateless"],
        cwd=launch,
        check=True,
    )
    assert not (sibling / ".agent-worktrees" / "config.yaml").exists()

    target = sibling
    if nested_kind is not None:
        target = sibling / "nested"
        target.mkdir()
        if nested_kind == "repository":
            subprocess.run(["git", "init", "--quiet"], cwd=target, check=True)
        else:
            git_dir = launch / ".git" / "modules" / "nested"
            git_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "init",
                    "--quiet",
                    "--separate-git-dir",
                    str(git_dir),
                    str(target),
                ],
                check=True,
            )
            assert (target / ".git").is_file()

    res = sr.validate_config_destination(
        str(target / "generated"),
        cwd=str(launch),
    )

    assert res.path is None
    assert res.bound is False
    assert res.stateless is True
    assert str(sibling) in res.error


def test_git_common_dir_ignores_inherited_repository_context(
    tmp_path,
    monkeypatch,
):
    checkout = tmp_path / "checkout"
    unrelated = tmp_path / "unrelated"
    checkout.mkdir()
    unrelated.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True)
    subprocess.run(["git", "init", "--quiet"], cwd=unrelated, check=True)

    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    monkeypatch.setenv("BENIGN_SETUP_CONTEXT", "preserved")
    captured: dict[str, str] = {}
    original_run = subprocess.run

    def recording_run(*args, **kwargs):
        captured.update(kwargs["env"])
        return original_run(*args, **kwargs)

    monkeypatch.setattr(sr.subprocess, "run", recording_run)

    assert sr._git_common_dir(checkout) == (checkout / ".git").resolve()
    assert "GIT_DIR" not in captured
    assert "GIT_WORK_TREE" not in captured
    assert captured["BENIGN_SETUP_CONTEXT"] == "preserved"


def test_git_toplevel_ignores_inherited_repository_context(
    tmp_path,
    monkeypatch,
):
    checkout = tmp_path / "checkout"
    unrelated = tmp_path / "unrelated"
    checkout.mkdir()
    unrelated.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True)
    subprocess.run(["git", "init", "--quiet"], cwd=unrelated, check=True)
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))

    resolved = sr._git_toplevel(str(checkout))
    assert resolved is not None
    assert Path(resolved).resolve() == checkout.resolve()


def test_config_root_validation_ignores_inherited_repository_context(
    tmp_path,
    monkeypatch,
):
    anchor = tmp_path / "harness"
    sibling = tmp_path / "harness-worktree"
    unrelated = tmp_path / "unrelated"
    anchor.mkdir()
    unrelated.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=anchor, check=True)
    subprocess.run(["git", "init", "--quiet"], cwd=unrelated, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=anchor,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=anchor,
        check=True,
    )
    (anchor / "README.md").write_text("harness\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=anchor, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=anchor, check=True)
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "sibling", str(sibling)],
        cwd=anchor,
        check=True,
    )
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))

    res = sr.resolve_config_root(
        _config("harness", stateless=True, anchor=str(anchor)),
        destination=str(sibling / "generated"),
        cwd=str(anchor),
    )

    assert res.path is None
    assert res.bound is False
    assert str(sibling) in res.error


def test_config_root_allows_explicit_machine_local_destination(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()
    machine_root = tmp_path / "home" / ".harness"

    res = sr.resolve_config_root(
        _config("harness", stateless=True, anchor=str(harness)),
        destination=str(machine_root),
        cwd=str(harness),
    )

    assert res.path == str(machine_root.resolve())
    assert res.source == "explicit"
    assert res.bound is True
    assert res.error is None


def test_config_root_cli_honors_machine_local_stateless_config(
    tmp_path,
    monkeypatch,
    capsys,
):
    from agent_worktrees import __main__ as main

    harness = tmp_path / "harness"
    (harness / ".git").mkdir(parents=True)
    monkeypatch.setattr(main.cfg, "active_project", lambda: "harness")
    monkeypatch.setattr(
        main.cfg,
        "load_config",
        lambda: _config("harness", stateless=True, anchor=str(harness)),
    )

    rc = main.cmd_config_root_dispatch(["--destination", str(harness)])

    assert rc == 3
    assert "inside stateless checkout" in capsys.readouterr().err


@pytest.mark.parametrize("launch_stateless", [False, True])
def test_config_root_cli_json_preserves_launch_statelessness(
    tmp_path,
    monkeypatch,
    capsys,
    launch_stateless,
):
    from agent_worktrees import __main__ as main

    launch = tmp_path / "launch"
    target = tmp_path / "target"
    launch.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=launch, check=True)
    subprocess.run(["git", "init", "--quiet"], cwd=target, check=True)
    (target / ".agent-worktrees").mkdir()
    (target / ".agent-worktrees" / "config.yaml").write_text(
        "stateless: true\n",
        encoding="utf-8",
    )
    main.cfg.set_active_project("launch")
    monkeypatch.setattr(
        main.cfg,
        "load_config",
        lambda: _config(
            "launch",
            stateless=launch_stateless,
            anchor=str(launch),
        ),
    )

    try:
        rc = main.cmd_config_root_dispatch([
            "--destination",
            str(target / "generated"),
            "--json",
        ])
    finally:
        main.cfg.set_active_project(None)

    data = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert data["config_root"] is None
    assert data["bound"] is False
    assert data["stateless"] is launch_stateless
    assert data["repo"] == "launch"
    assert "inside stateless checkout" in data["error"]


@pytest.fixture
def contaminated_main_project_resolution(tmp_path, monkeypatch):
    from agent_worktrees import __main__ as main

    anchor = tmp_path / "harness"
    sibling = tmp_path / "harness-worktree"
    unrelated = tmp_path / "unrelated"
    knowledge = tmp_path / "knowledge"
    anchor.mkdir()
    unrelated.mkdir()
    knowledge.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=anchor, check=True)
    subprocess.run(["git", "init", "--quiet"], cwd=unrelated, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=anchor,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=anchor,
        check=True,
    )
    (anchor / "README.md").write_text("harness\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=anchor, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=anchor, check=True)
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "sibling", str(sibling)],
        cwd=anchor,
        check=True,
    )
    configs = {
        "harness": _config(
            "harness",
            stateless=True,
            knowledge_repo="knowledge",
            anchor=str(anchor),
        ),
        "unrelated": _config("unrelated", anchor=str(unrelated)),
    }
    monkeypatch.setattr(
        main.inst,
        "read_projects_registry",
        lambda: {
            "projects": {
                "harness": {"anchor": str(anchor)},
                "unrelated": {"anchor": str(unrelated)},
            }
        },
    )
    monkeypatch.setattr(
        main.cfg,
        "load_config",
        lambda: configs[main.cfg.active_project()],
    )
    monkeypatch.setattr(sr, "_checkout_path", lambda name: (
        str(knowledge) if name == "knowledge" else None
    ))
    monkeypatch.chdir(sibling)
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    main.cfg.set_active_project(None)
    yield main, sibling, knowledge
    main.cfg.set_active_project(None)


def test_main_config_root_contamination_cannot_bypass_sibling_guard(
    contaminated_main_project_resolution,
    capsys,
):
    main, sibling, _knowledge = contaminated_main_project_resolution

    rc = main.main([
        "config-root",
        "--destination",
        str(sibling / "generated"),
    ])

    assert rc == 3
    assert main.cfg.active_project() == "harness"
    assert "inside stateless checkout" in capsys.readouterr().err


def test_main_state_root_contamination_cannot_redirect_project(
    contaminated_main_project_resolution,
    capsys,
):
    main, _sibling, knowledge = contaminated_main_project_resolution

    rc = main.main(["state-root"])

    assert rc == 0
    assert main.cfg.active_project() == "harness"
    assert Path(capsys.readouterr().out.strip()).resolve() == knowledge.resolve()


def test_config_root_explicit_destination_can_guard_without_adoption():
    from agent_worktrees import __main__ as main

    assert main._is_no_project_invocation(
        ["config-root", "--destination", "/tmp/harness"]
    )
    assert main._is_no_project_invocation(
        ["config-root", "--destination=/tmp/harness"]
    )
    assert not main._is_no_project_invocation(["config-root"])


def test_config_root_as_dict_shape(monkeypatch, tmp_path):
    machine_root = tmp_path / ".h"
    monkeypatch.setattr(cfg, "project_dir", lambda _name=None: machine_root)
    data = sr.resolve_config_root(_config("h")).as_dict()

    assert set(data) == {
        "config_root", "source", "repo", "stateless", "bound", "error",
    }
    assert data["config_root"] == str(machine_root.resolve())


def test_as_dict_shape(fake_checkouts):
    fake_checkouts["k"] = "/k"
    res = sr.resolve_state_root(
        _config("h", stateless=True, knowledge_repo="k")
    )
    d = res.as_dict()
    assert set(d) == {
        "state_root", "source", "repo", "stateless", "requires_external",
        "bound", "error",
    }
    assert d["state_root"] == "/k"
    assert d["requires_external"] is True
    assert d["bound"] is True


def test_bound_state_definition_routes_exact_content_categories():
    text = sr.state_repo_definition(sr.StateRoot(
        "/repos/personal-state", "knowledge_repo", "knowledge",
        True, True, True,
    ))
    assert (
        "generic, reusable, name-free configuration, skills, agents, "
        "`AGENTS.md`, and docs"
    ) in text
    assert "belong in the shared harness repo" in text
    assert (
        "Personal preferences, personal skills/config, private/reference data, "
        "and ambiguous or rootless writes"
    ) in text
    assert "belong in the state repo above" in text
def test_anchor_pair_rejects_rebound_knowledge_before_resolving(
    tmp_path, monkeypatch
):
    from agent_worktrees import tracking as tk

    harness = tmp_path / "harness"
    harness.mkdir()
    record = tk.WorktreeRecord(
        worktree_id="wt-harness",
        branch="worktree/wt-harness",
        worktree_path=str(harness),
        repo="harness",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        pair_id="pair1",
        pair_role="harness",
        pair_ref="test/old-private/@anchor",
        pair_kind="anchor",
    )
    monkeypatch.setattr(tk, "find_worktree_id_by_cwd", lambda _cwd: record.worktree_id)
    monkeypatch.setattr(tk, "load_record_by_id", lambda _wt_id: record)
    resolved = False

    def _resolve(_config):
        nonlocal resolved
        resolved = True
        raise AssertionError("mismatched anchor pair must not resolve the new binding")

    monkeypatch.setattr(sr, "resolve_state_root", _resolve)

    result = sr.resolve_pair(
        _config("harness", stateless=True, knowledge_repo="new-private"),
        cwd=str(harness),
    )

    assert result.paired is True
    assert "old-private" in result.error
    assert "new-private" in result.error
    assert resolved is False


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("pair-id-mismatch", "disagree on pair_id"),
        ("same-role", "complementary harness/knowledge roles"),
        ("kind-mismatch", "matching 'worktree' pair_kind"),
        ("current-ref-wrong-repo", "does not reference the sibling"),
        ("sibling-ref-wrong-worktree", "does not reference the current"),
        ("sibling-ref-wrong-repo", "does not reference the current"),
        ("unqualified-ref", "is not a qualified"),
        ("self-pair", "cannot pair with itself"),
        ("valid", None),
    ],
)
def test_worktree_pair_requires_coherent_reciprocal_records(
    tmp_path, monkeypatch, case, message
):
    from agent_worktrees import tracking as tk

    def _record(
        wt_id,
        *,
        repo,
        role,
        path,
        pair_ref,
    ):
        return tk.WorktreeRecord(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=str(path),
            repo=repo,
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
            pair_id="pair-1",
            pair_role=role,
            pair_ref=pair_ref,
            pair_kind="worktree",
        )

    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    current = _record(
        "wt-harness",
        repo="harness",
        role="harness",
        path=harness,
        pair_ref="test/knowledge/wt-knowledge",
    )
    sibling = _record(
        "wt-knowledge",
        repo="knowledge",
        role="knowledge",
        path=knowledge,
        pair_ref="test/harness/wt-harness",
    )

    if case == "pair-id-mismatch":
        sibling.pair_id = "pair-2"
    elif case == "same-role":
        sibling.pair_role = "harness"
    elif case == "kind-mismatch":
        sibling.pair_kind = "anchor"
    elif case == "current-ref-wrong-repo":
        current.pair_ref = "test/wrong-repo/wt-knowledge"
    elif case == "sibling-ref-wrong-worktree":
        sibling.pair_ref = "test/harness/wt-other"
    elif case == "sibling-ref-wrong-repo":
        sibling.pair_ref = "test/wrong-repo/wt-harness"
    elif case == "unqualified-ref":
        current.pair_ref = "wt-knowledge"
    elif case == "self-pair":
        sibling.repo = current.repo
        sibling.worktree_id = current.worktree_id
        current.pair_ref = "test/harness/wt-harness"

    monkeypatch.setattr(tk, "find_worktree_id_by_cwd", lambda _cwd: current.worktree_id)
    monkeypatch.setattr(tk, "load_record_by_id", lambda _wt_id: current)
    monkeypatch.setattr(tk, "find_paired_record", lambda _record: sibling)

    result = sr.resolve_pair(None, cwd=str(harness))

    assert result.current is not None
    assert result.current.worktree_id == "wt-harness"
    if message is None:
        assert result.error is None
        assert result.sibling is not None
        assert result.sibling.worktree_id == "wt-knowledge"
        assert result.pair_id == "pair-1"
    else:
        assert result.sibling is None
        assert message in result.error


# ---------------------------------------------------------------------------
# state-root --pair: the citadel paired-worktree resolver (#957).
# ---------------------------------------------------------------------------

class TestStateRootPairCLI:
    """Drive ``cmd_state_root_dispatch(['--pair', ...])`` end to end."""

    def _pair_records(self, tracking_dir, harness_path, knowledge_path):
        from agent_worktrees import tracking as tk

        def _rec(wt_id, role, repo, wp, ref):
            rec = tk.WorktreeRecord(
                worktree_id=wt_id, branch=f"worktree/{wt_id}", worktree_path=wp,
                repo=repo, machine="test", platform="wsl",
                started_at="2026-06-01T10:00:00",
                last_resumed_at="2026-06-01T10:00:00", resume_count=0,
                title=None, status="active", completed_at=None, sessions=[],
                pair_id="pair1", pair_role=role, pair_ref=ref, pair_kind="worktree",
            )
            tk.save_record(rec, tracking_dir / f"{wt_id}.yaml")

        _rec("wt-harness", "harness", "harness", str(harness_path),
             "test/knowledge/wt-knowledge")
        _rec("wt-knowledge", "knowledge", "knowledge", str(knowledge_path),
             "test/harness/wt-harness")

    def test_pair_prints_sibling_path(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        harness = tmp_path / "harness"
        knowledge = tmp_path / "knowledge"
        harness.mkdir()
        knowledge.mkdir()
        self._pair_records(tracking_dir, harness, knowledge)
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr("agent_worktrees.__main__.os.getcwd", lambda: str(harness))

        rc = cmd_state_root_dispatch(["--pair"])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(knowledge)

    def test_pair_json(self, tmp_path, monkeypatch, capsys):
        import json as _json

        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        harness = tmp_path / "harness"
        knowledge = tmp_path / "knowledge"
        harness.mkdir()
        knowledge.mkdir()
        self._pair_records(tracking_dir, harness, knowledge)
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr("agent_worktrees.__main__.os.getcwd", lambda: str(harness))

        rc = cmd_state_root_dispatch(["--pair", "--json"])
        data = _json.loads(capsys.readouterr().out)
        assert rc == 0
        assert data["paired"] is True and data["pair_id"] == "pair1"
        assert data["self"]["role"] == "harness"
        assert data["sibling"]["worktree_id"] == "wt-knowledge"
        assert data["sibling"]["role"] == "knowledge"
        assert data["sibling"]["path"] == str(knowledge)

    def test_pair_untracked_cwd_exits_3(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr(
            "agent_worktrees.__main__.os.getcwd", lambda: str(tmp_path / "nowhere")
        )
        rc = cmd_state_root_dispatch(["--pair"])
        assert rc == 3

    def test_pair_unpaired_worktree_exits_3(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        solo = tmp_path / "solo"
        solo.mkdir()
        rec = tk.WorktreeRecord(
            worktree_id="solo", branch="worktree/solo", worktree_path=str(solo),
            repo="r", machine="test", platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0,
            title=None, status="active", completed_at=None, sessions=[],
        )
        tk.save_record(rec, tracking_dir / "solo.yaml")
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr("agent_worktrees.__main__.os.getcwd", lambda: str(solo))
        rc = cmd_state_root_dispatch(["--pair"])
        assert rc == 3


# ---------------------------------------------------------------------------
# config_source_anchors -- the E1e knowledge-overlay (config-graft) seam.
# ---------------------------------------------------------------------------

def test_config_sources_non_stateless_is_base_only(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: "/work/tree")
    srcs = sr.config_source_anchors(_config("dotfiles"))
    assert [(s.anchor, s.origin) for s in srcs] == [("/work/tree", "harness")]


def test_config_sources_stateless_grafts_knowledge_overlay(fake_checkouts):
    fake_checkouts["citadel-knowledge"] = "/repos/knowledge"
    srcs = sr.config_source_anchors(
        _config("citadel-harness", stateless=True,
                knowledge_repo="citadel-knowledge"),
        base_anchor="/repos/harness",
    )
    assert [(s.anchor, s.origin) for s in srcs] == [
        ("/repos/harness", "harness"),
        ("/repos/knowledge", "knowledge"),
    ]


def test_config_sources_dedup_when_knowledge_equals_base(fake_checkouts):
    # Degenerate case: the knowledge checkout is the base -- no duplicate overlay.
    fake_checkouts["k"] = "/repos/harness"
    srcs = sr.config_source_anchors(
        _config("h", stateless=True, knowledge_repo="k"),
        base_anchor="/repos/harness",
    )
    assert [s.anchor for s in srcs] == ["/repos/harness"]


def test_config_sources_unbound_stateless_is_base_only(fake_checkouts):
    # require-external but no bound knowledge repo -> base only (no overlay).
    srcs = sr.config_source_anchors(
        _config("h", stateless=True, knowledge_repo=""),
        base_anchor="/repos/harness",
    )
    assert [s.anchor for s in srcs] == ["/repos/harness"]


# ---------------------------------------------------------------------------
# state_repo_definition -- the sessionStart "the user's state repo" injection.
# ---------------------------------------------------------------------------

def test_state_repo_definition_self_hosted_names_path_and_current_repo():
    res = sr.StateRoot("/work/tree", "launch_repo", "dotfiles", False, False, True)
    text = sr.state_repo_definition(res)
    assert "**The user's state repo**" in text
    assert "`/work/tree`" in text
    assert "self-hosted" in text
    assert "\n" not in text  # single self-contained paragraph


def test_state_repo_definition_stateless_names_knowledge_repo():
    res = sr.StateRoot("/repos/knowledge", "knowledge_repo", "kn", True, True, True)
    text = sr.state_repo_definition(res)
    assert "`/repos/knowledge`" in text
    assert "bound knowledge repo" in text


def test_state_repo_definition_explicit_names_repo():
    res = sr.StateRoot("/repos/x", "explicit", "x", False, False, True)
    text = sr.state_repo_definition(res)
    assert "`/repos/x`" in text
    assert "'x'" in text


def test_state_repo_definition_unbound_has_no_path_and_warns():
    res = sr.StateRoot(None, "knowledge_repo", "", True, True, False,
                       error="unbound")
    text = sr.state_repo_definition(res)
    assert "not bound on this machine" in text
    assert "`" not in text  # no backtick-quoted path when unresolved
    assert "**The user's state repo**" in text


def test_state_repo_definition_bound_knowledge_carries_write_routing():
    # A bound knowledge repo => harness and state repo are distinct, so the
    # definition adds the "where changes go" routing clause.
    res = sr.StateRoot("/repos/knowledge", "knowledge_repo", "kn", True, True, True)
    text = sr.state_repo_definition(res)
    assert "Where changes go" in text
    assert "shared harness" in text
    assert "harness repo (your current checkout)" in text
    for shared_category in (
        "generic, reusable, name-free configuration",
        "skills",
        "agents",
        "`AGENTS.md`",
        "docs",
    ):
        assert shared_category in text
    for state_category in (
        "Personal preferences",
        "personal skills/config",
        "private/reference data",
        "ambiguous or rootless writes",
    ):
        assert state_category in text
    assert "belong in the state repo above" in text
    assert len(text) <= 700


def test_state_repo_definition_self_hosted_has_no_write_routing():
    # Self-hosted: one checkout, so NO routing clause (would be noise).
    res = sr.StateRoot("/work/tree", "launch_repo", "dotfiles", False, False, True)
    text = sr.state_repo_definition(res)
    assert "Where changes go" not in text


def test_state_repo_definition_explicit_has_no_write_routing():
    res = sr.StateRoot("/repos/x", "explicit", "x", False, False, True)
    text = sr.state_repo_definition(res)
    assert "Where changes go" not in text
