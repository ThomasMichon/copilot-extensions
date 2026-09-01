"""Tests for the harness-knowledge bind_knowledge configurator."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

_MOD = Path(__file__).resolve().parents[1] / "skills" / "binding-knowledge" / "scripts" / "bind_knowledge.py"
_spec = importlib.util.spec_from_file_location("bind_knowledge", _MOD)
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


# --- set_top_yaml_key ---------------------------------------------------------

def test_insert_after_comment_header():
    text = "# header comment\n# more\nrepo_name: h\n"
    out = bk.set_top_yaml_key(text, "knowledge_repo", "k")
    lines = out.splitlines()
    # inserted after the leading comment block, before repo_name
    assert lines[0].startswith("#") and lines[1].startswith("#")
    assert "knowledge_repo: k" in lines
    assert out.index("knowledge_repo") < out.index("repo_name")
    assert out.endswith("\n")


def test_replace_existing_key():
    text = "repo_name: h\nknowledge_repo: old\nother: 1\n"
    out = bk.set_top_yaml_key(text, "knowledge_repo", "new")
    assert "knowledge_repo: new" in out
    assert "old" not in out
    assert out.count("knowledge_repo:") == 1
    assert "other: 1" in out  # rest preserved


def test_replace_preserves_comments():
    text = "# c1\nknowledge_repo: old  # inline\nrepo_name: h\n"
    out = bk.set_top_yaml_key(text, "knowledge_repo", "new")
    assert "# c1" in out
    assert "knowledge_repo: new" in out
    assert "old" not in out


# --- bind (end to end, machine-local) -----------------------------------------

def test_bind_writes_pointer_without_instruction_fragment(tmp_path: Path):
    home = tmp_path / "home"
    summary = bk.bind("citadel-harness", "citadel-knowledge", "C:/k",
                      home=home, harness_path="C:/h")
    cfg = home / ".citadel-harness" / "config.yaml"
    frag = home / ".citadel-harness" / "knowledge-binding.md"
    old_frag = home / ".citadel-harness" / ".github" / "instructions" / "knowledge-binding.instructions.md"
    assert cfg.exists()
    assert not frag.exists(), "agent-worktrees owns live state context"
    assert not old_frag.exists(), "the fragment must not land in the auto-loaded instructions dir"
    assert "knowledge_repo: citadel-knowledge" in cfg.read_text()
    assert "repo_name: citadel-harness" in cfg.read_text()  # seeded
    assert summary["knowledge_repo"] == "citadel-knowledge"


def test_bind_retires_stale_auto_loaded_fragment(tmp_path: Path):
    # A prior bind wrote the auto-loaded file; re-binding retires it (marker-guarded).
    home = tmp_path / "home"
    old_dir = home / ".citadel-harness" / ".github" / "instructions"
    old_dir.mkdir(parents=True)
    old_frag = old_dir / "knowledge-binding.instructions.md"
    old_frag.write_text(f"{bk.MANAGED_MARKER}\n# stale binding\n", encoding="utf-8")

    bk.bind("citadel-harness", "kn", "C:/k", home=home, harness_path="C:/h")

    assert not old_frag.exists(), "stale auto-loaded fragment must be retired on re-bind"
    assert not (home / ".citadel-harness" / "knowledge-binding.md").exists()


def test_bind_retires_stale_hook_fragment(tmp_path: Path):
    home = tmp_path / "home"
    base = home / ".citadel-harness"
    base.mkdir(parents=True)
    fragment = base / "knowledge-binding.md"
    fragment.write_text(f"{bk.MANAGED_MARKER}\n# stale binding\n", encoding="utf-8")

    bk.bind("citadel-harness", "kn", "C:/k", home=home, harness_path="C:/h")

    assert not fragment.exists()


def test_bind_leaves_unmarked_user_instructions(tmp_path: Path):
    # An unmarked user file in the instructions dir must never be deleted.
    home = tmp_path / "home"
    old_dir = home / ".citadel-harness" / ".github" / "instructions"
    old_dir.mkdir(parents=True)
    user_file = old_dir / "knowledge-binding.instructions.md"
    user_file.write_text("# my own notes, not ours\n", encoding="utf-8")

    bk.bind("citadel-harness", "kn", "C:/k", home=home, harness_path="C:/h")

    assert user_file.exists(), "an unmarked user file must never be deleted"


def test_bind_preserves_existing_config(tmp_path: Path):
    home = tmp_path / "home"
    base = home / ".citadel-harness"
    base.mkdir(parents=True)
    (base / "config.yaml").write_text(
        "# my config\nrepo_name: citadel-harness\nrepos:\n  citadel-harness:\n    anchor: C:/h\n",
        encoding="utf-8",
    )
    bk.bind("citadel-harness", "kn", "C:/k", home=home)
    text = (base / "config.yaml").read_text()
    assert "# my config" in text
    assert "anchor: C:/h" in text  # existing structure preserved
    assert "knowledge_repo: kn" in text


def test_bind_idempotent_repoint(tmp_path: Path):
    home = tmp_path / "home"
    bk.bind("h", "k1", "C:/k1", home=home)
    bk.bind("h", "k2", "C:/k2", home=home)  # re-point
    text = (home / ".h" / "config.yaml").read_text()
    assert text.count("knowledge_repo:") == 1
    assert "knowledge_repo: k2" in text
    assert "k1" not in text


# --- canonical registration readiness ---------------------------------------

def _completed(
    args: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_registered_worktree_reports_canonical_readiness(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        bk,
        "knowledge_origin",
        lambda _path: "https://github.com/example/knowledge.git",
    )
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")

    def fake_run(_command, args, **_kwargs):
        if args == ["repos", "list", "--json"]:
            return _completed(
                args,
                stdout=json.dumps(
                    {
                        "repos": [
                            {
                                "name": "knowledge",
                                "class": "worktree",
                                "remote": "https://github.com/example/knowledge.git",
                                "default_branch": "main",
                                "resolved_account": "example",
                                "paths": {
                                    bk._current_platform_key(): str(knowledge),
                                },
                            }
                        ]
                    }
                ),
            )
        if args == [
            "repos",
            "gh",
            "example/knowledge",
            "--",
            "api",
            "user",
            "--jq",
            ".login",
        ]:
            return _completed(args, stdout="example\n")
        raise AssertionError(args)

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
    )

    assert registration["status"] == "ready"
    assert registration["canonical"] is True
    assert registration["path_source"] == "canonical_registry"
    assert registration["class"] == "worktree"
    assert registration["resolved_path"] == str(knowledge)
    assert registration["remote"] == "https://github.com/example/knowledge.git"
    assert registration["default_branch"] == "main"
    assert registration["account"] == "example"
    assert registration["registration_command"] == ""


def test_unregistered_fallback_is_not_canonical_readiness(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        bk,
        "knowledge_origin",
        lambda _path: "https://github.com/example/knowledge.git",
    )
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")

    def fake_run(_command, args, **_kwargs):
        if args == ["repos", "list", "--json"]:
            return _completed(args, stdout='{"repos":[]}')
        if args == ["repos", "find", "knowledge", "--json"]:
            return _completed(
                args,
                stdout=json.dumps(
                    {"name": "knowledge", "path": str(knowledge)}
                ),
            )
        raise AssertionError(args)

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
    )

    assert registration["status"] == "missing"
    assert registration["canonical"] is False
    assert registration["path_source"] == "fallback_discovery"
    assert registration["resolved_path"] == str(knowledge)
    assert registration["account"] == ""
    assert registration["registration_argv"] == [
        "repos",
        "add",
        "knowledge",
        str(knowledge),
        "--class",
        "worktree",
        "--remote",
        "https://github.com/example/knowledge.git",
        "--default-branch",
        "main",
    ]


def test_registered_path_and_class_mismatch_has_repair_command(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    wrong = tmp_path / "wrong"
    monkeypatch.setattr(bk, "knowledge_origin", lambda _path: "")
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "")

    def fake_run(_command, args, **_kwargs):
        assert args == ["repos", "list", "--json"]
        return _completed(
            args,
            stdout=json.dumps(
                {
                    "repos": [
                        {
                            "name": "knowledge",
                            "class": "reference",
                            "remote": "",
                            "default_branch": "",
                            "resolved_account": None,
                            "paths": {
                                bk._current_platform_key(): str(wrong),
                            },
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
    )

    assert registration["status"] == "mismatch"
    assert registration["canonical"] is True
    assert "expected worktree" in registration["reason"]
    assert str(wrong) in registration["reason"]
    assert registration["registration_argv"] == [
        "repos",
        "add",
        "knowledge",
        str(knowledge),
        "--class",
        "worktree",
    ]


def test_case_only_registry_collision_is_not_ready_or_auto_repaired(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(bk, "knowledge_origin", lambda _path: "")
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "")

    def fake_run(_command, args, **_kwargs):
        assert args == ["repos", "list", "--json"]
        return _completed(
            args,
            stdout=json.dumps(
                {
                    "repos": [
                        {
                            "name": "Knowledge",
                            "class": "worktree",
                            "remote": "",
                            "default_branch": "",
                            "resolved_account": None,
                            "paths": {
                                bk._current_platform_key(): str(knowledge),
                            },
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
    )

    assert registration["status"] == "mismatch"
    assert registration["canonical"] is False
    assert registration["path_source"] == "canonical_registry"
    assert registration["registration_argv"] == []
    assert "differs only by case" in registration["reason"]


def test_stale_default_branch_is_repaired_without_persisting_derived_account(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        bk,
        "knowledge_origin",
        lambda _path: "https://github.com/example/knowledge.git",
    )
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")

    def fake_run(_command, args, **_kwargs):
        if args == ["repos", "list", "--json"]:
            return _completed(
                args,
                stdout=json.dumps(
                    {
                        "repos": [
                            {
                                "name": "knowledge",
                                "class": "worktree",
                                "remote": "https://github.com/example/knowledge.git",
                                "default_branch": "feature",
                                "account": "",
                                "resolved_account": "example",
                                "paths": {
                                    bk._current_platform_key(): str(knowledge),
                                },
                            }
                        ]
                    }
                ),
            )
        if args == [
            "repos",
            "gh",
            "example/knowledge",
            "--",
            "api",
            "user",
            "--jq",
            ".login",
        ]:
            return _completed(args, stdout="example\n")
        raise AssertionError(args)

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
    )

    assert registration["status"] == "mismatch"
    assert "expected main" in registration["reason"]
    assert registration["account"] == "example"
    assert registration["registration_argv"][-2:] == [
        "--default-branch",
        "main",
    ]
    assert "--account" not in registration["registration_argv"]


def test_unusable_resolved_account_blocks_registration_readiness(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        bk,
        "knowledge_origin",
        lambda _path: "https://github.com/example-org/knowledge.git",
    )
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")

    def fake_run(_command, args, **_kwargs):
        if args == ["repos", "list", "--json"]:
            return _completed(
                args,
                stdout=json.dumps(
                    {
                        "repos": [
                            {
                                "name": "knowledge",
                                "class": "worktree",
                                "remote": "https://github.com/example-org/knowledge.git",
                                "default_branch": "main",
                                "account": "",
                                "resolved_account": "example-org",
                                "paths": {
                                    bk._current_platform_key(): str(knowledge),
                                },
                            }
                        ]
                    }
                ),
            )
        if args[:3] == ["repos", "gh", "example-org/knowledge"]:
            return _completed(
                args,
                returncode=1,
                stderr="no token for account example-org",
            )
        raise AssertionError(args)

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
    )

    assert registration["status"] == "mismatch"
    assert registration["account_status"] == "not_ready"
    assert "not usable" in registration["reason"]
    assert "pass --account" in registration["reason"]
    assert registration["registration_argv"] == []


def test_account_override_repairs_unusable_owner_fallback(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    account = "contributor"
    stored_account = ""
    monkeypatch.setattr(
        bk,
        "knowledge_origin",
        lambda _path: "https://github.com/example-org/knowledge.git",
    )
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")

    def fake_run(_command, args, **_kwargs):
        nonlocal stored_account
        if args == ["repos", "list", "--json"]:
            return _completed(
                args,
                stdout=json.dumps(
                    {
                        "repos": [
                            {
                                "name": "knowledge",
                                "class": "worktree",
                                "remote": "https://github.com/example-org/knowledge.git",
                                "default_branch": "main",
                                "account": stored_account,
                                "resolved_account": stored_account or "example-org",
                                "paths": {
                                    bk._current_platform_key(): str(knowledge),
                                },
                            }
                        ]
                    }
                ),
            )
        if args[:3] == ["repos", "gh", "example-org/knowledge"]:
            login = stored_account or "example-org"
            if login == account:
                return _completed(args, stdout=f"{account}\n")
            return _completed(
                args,
                returncode=1,
                stderr=f"no token for account {login}",
            )
        if args[:3] == ["repos", "add", "knowledge"]:
            index = args.index("--account")
            stored_account = args[index + 1]
            return _completed(args)
        raise AssertionError(args)

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    registration = bk.ensure_registration(
        "knowledge",
        str(knowledge),
        "agent-worktrees",
        account,
    )

    assert stored_account == account
    assert registration["status"] == "ready"
    assert registration["account"] == account
    assert registration["account_status"] == "ready"


def test_default_branch_uses_remote_symref_when_origin_head_is_missing(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "symbolic-ref" in command:
            return _completed(command, returncode=1)
        if "ls-remote" in command:
            return _completed(
                command,
                stdout="ref: refs/heads/trunk\tHEAD\nabc123\tHEAD\n",
            )
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert bk.knowledge_default_branch(str(knowledge)) == "trunk"
    assert any("ls-remote" in command for command in calls)


def test_default_branch_prefers_remote_and_preserves_slashes(
    tmp_path: Path,
    monkeypatch,
):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()

    def fake_run(command, **_kwargs):
        if "ls-remote" in command:
            return _completed(
                command,
                stdout="ref: refs/heads/release/stable\tHEAD\nabc123\tHEAD\n",
            )
        if "symbolic-ref" in command:
            return _completed(command, stdout="origin/main\n")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert bk.knowledge_default_branch(str(knowledge)) == "release/stable"


def test_http_remote_userinfo_is_removed():
    assert bk.sanitize_remote(
        "https://user:secret@example.com/owner/repo.git"
    ) == "https://example.com/owner/repo.git"
    assert bk.sanitize_remote(
        "git@github.com:owner/repo.git"
    ) == "git@github.com:owner/repo.git"


def test_windows_powershell_catalog_path_uses_host(monkeypatch):
    if bk.os.name != "nt":
        return
    monkeypatch.setattr(
        bk.shutil,
        "which",
        lambda name: r"C:\Program Files\PowerShell\7\pwsh.exe"
        if name == "pwsh.exe"
        else None,
    )

    command = bk._agent_worktrees_command(
        r"C:\payload\agent-worktrees.ps1",
        ["repos", "list", "--json"],
    )

    assert command == [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "-NoProfile",
        "-NoLogo",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\payload\agent-worktrees.ps1",
        "repos",
        "list",
        "--json",
    ]
    rendered = bk._render_command(command)
    assert rendered.startswith(
        "& 'C:\\Program Files\\PowerShell\\7\\pwsh.exe' '-NoProfile'"
    )


def test_ambient_auth_fallback_is_not_account_readiness(monkeypatch):
    def fake_run(_command, args, **_kwargs):
        return _completed(
            args,
            stdout="example\n",
            stderr="could not mint a gh token for 'example'; using ambient auth\n",
        )

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    status, reason = bk._inspect_github_account(
        "agent-worktrees",
        "https://github.com/example/knowledge.git",
        "example",
    )

    assert status == "not_ready"
    assert "ambient auth" in reason


def test_registration_without_agent_worktrees_is_unverified(tmp_path: Path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()

    registration = bk.inspect_registration(
        "knowledge",
        str(knowledge),
        "",
    )

    assert registration["status"] == "unverified"
    assert registration["path_source"] == "unverified"
    assert registration["registration_command"] == ""
    assert "not supplied" in registration["reason"]


def test_register_repairs_then_binds_and_verifies_state_root(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    registered = False
    monkeypatch.setattr(
        bk,
        "knowledge_origin",
        lambda _path: "https://github.com/example/knowledge.git",
    )
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")

    def fake_run(_command, args, **_kwargs):
        nonlocal registered
        if args == ["repos", "list", "--json"]:
            repos = []
            if registered:
                repos.append(
                    {
                        "name": "knowledge",
                        "class": "worktree",
                        "remote": "https://github.com/example/knowledge.git",
                        "default_branch": "main",
                        "resolved_account": "example",
                        "paths": {
                            bk._current_platform_key(): str(knowledge),
                        },
                    }
                )
            return _completed(args, stdout=json.dumps({"repos": repos}))
        if args == [
            "repos",
            "gh",
            "example/knowledge",
            "--",
            "api",
            "user",
            "--jq",
            ".login",
        ]:
            return _completed(args, stdout="example\n")
        if args == ["repos", "find", "knowledge", "--json"]:
            return _completed(args, returncode=1, stderr="not found")
        if args[:3] == ["repos", "add", "knowledge"]:
            registered = True
            return _completed(args)
        if args == ["state-root", "--json"]:
            return _completed(
                args,
                stdout=json.dumps(
                    {
                        "bound": True,
                        "repo": "knowledge",
                        "state_root": str(knowledge),
                        "error": None,
                    }
                ),
            )
        raise AssertionError(args)

    monkeypatch.setattr(bk, "_run_agent_worktrees", fake_run)

    summary = bk.bind(
        "harness",
        "knowledge",
        str(knowledge),
        home=home,
        harness_path=str(harness),
        assemble_plugins=False,
        agent_worktrees_path="agent-worktrees",
        register=True,
    )

    assert registered is True
    assert summary["registration"]["status"] == "ready"
    assert summary["state_root"]["status"] == "ready"
    assert "knowledge_repo: knowledge" in (
        home / ".harness" / "config.yaml"
    ).read_text(encoding="utf-8")


# --- bind assembles the personal-plugin overlay (#955) ------------------------

def test_bind_assembles_plugins_when_paths_known(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    (knowledge / ".ai").mkdir(parents=True)
    (knowledge / ".github" / "copilot").mkdir(parents=True)
    (knowledge / ".github" / "copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {"kn": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"skill@kn": True},
    }), encoding="utf-8")

    overlay = harness / ".github" / "copilot" / "settings.local.json"
    summary = {
        "action": "composed",
        "paired": False,
        "changed": True,
        "count": 1,
        "settings_local": str(overlay),
        "harness_path": str(harness),
        "knowledge_path": str(knowledge),
        "marketplaces": ["kn"],
        "enabled_plugins": ["skill@kn"],
        "conflicts": {"marketplaces": [], "enabled_plugins": []},
    }

    def fake_run(command, **_kwargs):
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 2, "", "")
        assert command[1:6] == [
            "knowledge",
            "compose-plugins",
            "--harness-path",
            str(harness),
            "--knowledge-path",
        ]
        assert command[6:] == [str(knowledge), "--json"]
        overlay.parent.mkdir(parents=True)
        overlay.write_text(
            json.dumps({"enabledPlugins": {"skill@kn": True}}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, json.dumps(summary), "")

    monkeypatch.setattr(shutil, "which", lambda _name: "agent-worktrees")
    monkeypatch.setattr(subprocess, "run", fake_run)

    summary = bk.bind("citadel-harness", "kn-repo", str(knowledge),
                      home=home, harness_path=str(harness))
    # The overlay was written into the harness checkout.
    assert overlay.exists()
    out = json.loads(overlay.read_text())
    assert out["enabledPlugins"] == {"skill@kn": True}
    assert summary["plugins"]["count"] == 1


def test_bind_skips_assembly_without_harness_path(tmp_path: Path):
    home = tmp_path / "home"
    summary = bk.bind("h", "k", "C:/k", home=home)  # no harness_path
    assert "plugins" not in summary


# --- personal issue routing --------------------------------------------------

def _knowledge_repo(path: Path, remote: str | None = None) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote],
            check=True,
        )
    return path


def test_github_origin_is_a_ready_default_issue_route(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "git@github.com:example/private-knowledge.git",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["source"] == "origin"
    assert routing["provider"] == "github"
    assert routing["repo"] == "example/private-knowledge"


def test_non_github_origin_requires_explicit_issue_routing(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://example@dev.azure.com/example/Project/_git/knowledge",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "routing_required"
    assert routing["origin_provider"] == "azure-devops"
    assert routing["repo"] == ""


def test_explicit_github_route_makes_non_github_origin_ready(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://example.visualstudio.com/Project/_git/knowledge",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    original = (
        "# repository-owned routing\n"
        "issues:\n"
        "  provider: github\n"
        "  repo: example/personal-backlog\n"
    )
    config.write_text(original, encoding="utf-8")

    summary = bk.bind(
        "harness",
        "knowledge",
        str(knowledge),
        home=tmp_path / "home",
        assemble_plugins=False,
    )

    assert summary["issues"]["status"] == "ready"
    assert summary["issues"]["source"] == "config"
    assert summary["issues"]["repo"] == "example/personal-backlog"
    assert config.read_text(encoding="utf-8") == original


def test_commented_issues_header_keeps_nested_route(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://example@dev.azure.com/example/Project/_git/knowledge",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "issues:  # personal backlog\n"
        "  provider: github\n"
        "  repo: example/personal-backlog\n",
        encoding="utf-8",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["repo"] == "example/personal-backlog"


def test_empty_issues_mapping_uses_origin_fallback(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://github.com/example/private-knowledge.git",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("issues: {} # use the default\n", encoding="utf-8")

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["source"] == "config+origin"
    assert routing["repo"] == "example/private-knowledge"


def test_empty_nested_issues_block_uses_origin_fallback(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://github.com/example/private-knowledge.git",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("issues:\n# next section\n", encoding="utf-8")

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["source"] == "config+origin"


def test_hash_inside_quoted_repo_is_not_a_comment(tmp_path: Path):
    knowledge = _knowledge_repo(tmp_path / "knowledge")
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        'issues: { provider: github, repo: "example/personal#backlog" }\n',
        encoding="utf-8",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["repo"] == "example/personal#backlog"


def test_inline_unsupported_issue_provider_is_reported(tmp_path: Path):
    knowledge = _knowledge_repo(tmp_path / "knowledge")
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "issues: { provider: azure-devops, repo: Project/Backlog }\n",
        encoding="utf-8",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "unsupported"
    assert routing["provider"] == "azure-devops"


def test_nested_issue_mapping_does_not_override_direct_route(tmp_path: Path):
    knowledge = _knowledge_repo(tmp_path / "knowledge")
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "issues:\n"
        "  provider: github\n"
        "  repo: example/personal-backlog\n"
        "  templates:\n"
        "    repo: wrong/nested-value\n",
        encoding="utf-8",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["repo"] == "example/personal-backlog"


def test_invalid_github_repo_shape_requires_routing_fix(tmp_path: Path):
    knowledge = _knowledge_repo(tmp_path / "knowledge")
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "issues: { provider: github, repo: https://github.com/o/r/issues }\n",
        encoding="utf-8",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "routing_required"
    assert routing["reason"] == "GitHub issue repo must use owner/name form"


def test_unreadable_issue_config_reports_unknown(tmp_path: Path):
    knowledge = _knowledge_repo(tmp_path / "knowledge")
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_bytes(b"issues:\n  repo: example/\xff\n")

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "unknown"
    assert routing["config"] == str(config.resolve())


def test_unrecognized_issue_block_reports_unknown(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://github.com/example/private-knowledge.git",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "issues:\n"
        "  - provider: github\n"
        "    repo: example/personal-backlog\n",
        encoding="utf-8",
    )

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "unknown"
    assert "malformed" in routing["reason"]


def test_missing_origin_requires_explicit_issue_routing(tmp_path: Path):
    knowledge = _knowledge_repo(tmp_path / "knowledge")

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "routing_required"
    assert routing["origin_provider"] == "missing"


def test_missing_knowledge_path_is_not_resolved_from_cwd():
    routing = bk.inspect_issue_routing("")

    assert routing["status"] == "unknown"
    assert routing["config"] == ""


def test_nested_directory_does_not_inherit_parent_repo_origin(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://github.com/example/private-knowledge.git",
    )
    nested = knowledge / "nested"
    nested.mkdir()

    routing = bk.inspect_issue_routing(str(nested))

    assert routing["status"] == "routing_required"
    assert routing["origin_provider"] == "missing"


def test_azure_devops_ssh_origin_is_classified():
    assert bk.classify_origin(
        "git@ssh.dev.azure.com:v3/example/Project/knowledge"
    ) == ("azure-devops", "")


def test_scheme_ssh_github_origin_with_port_is_classified():
    assert bk.classify_origin(
        "ssh://git@github.com:22/example/private-knowledge.git"
    ) == ("github", "example/private-knowledge")


def test_provider_only_config_uses_github_origin_with_mixed_source(tmp_path: Path):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://github.com/example/private-knowledge.git",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("issues:\n  provider: github\n", encoding="utf-8")

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "ready"
    assert routing["source"] == "config+origin"
    assert routing["repo"] == "example/private-knowledge"


def test_provider_only_config_without_github_origin_stays_config_sourced(
    tmp_path: Path,
):
    knowledge = _knowledge_repo(
        tmp_path / "knowledge",
        "https://example.visualstudio.com/Project/_git/knowledge",
    )
    config = knowledge / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("issues:\n  provider: github\n", encoding="utf-8")

    routing = bk.inspect_issue_routing(str(knowledge))

    assert routing["status"] == "routing_required"
    assert routing["source"] == "config"
    assert routing["repo"] == ""
