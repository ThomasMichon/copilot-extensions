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
