"""Tests for scan-customizations.py -- the loaded-set purview + external-plugin
collision remediation (the reviewing-customizations enhancement).

Stdlib + pytest only. The script has a hyphenated filename, so it is imported
from its path via importlib.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

_SCRIPT = Path(__file__).with_name("scan-customizations.py")
_spec = importlib.util.spec_from_file_location("scan_customizations", _SCRIPT)
scan = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = scan          # dataclasses introspection needs this
_spec.loader.exec_module(scan)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _skill(dir_: Path, name: str, *, triggers: list[str] | None = None,
           folder: str | None = None, desc: str = "A test skill.") -> None:
    folder = folder or name
    d = dir_ / folder
    d.mkdir(parents=True, exist_ok=True)
    trig = ""
    if triggers:
        trig = "\n  Trigger phrases include:\n" + "\n".join(
            f"  - '{t}'" for t in triggers)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: >\n  {desc}{trig}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _settings(repo: Path, enabled: dict, marketplaces: dict) -> None:
    p = repo / ".github" / "copilot" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "enabledPlugins": enabled, "extraKnownMarketplaces": marketplaces,
    }), encoding="utf-8")


def _installed_plugin(root: Path, mkt: str, name: str, *,
                      triggers: list[str] | None = None,
                      repository: str | None = None) -> None:
    pdir = root / mkt / name
    (pdir / "skills").mkdir(parents=True, exist_ok=True)
    _skill(pdir / "skills", name, triggers=triggers)
    if repository:
        (pdir / "plugin.json").write_text(
            json.dumps({"name": name, "repository": repository}), encoding="utf-8")


# ---------------------------------------------------------------------------
# assemble_enabled_plugins
# ---------------------------------------------------------------------------

def test_assemble_directory_marketplace_is_controlled(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".ai" / "cap" / "skills").mkdir(parents=True)
    _skill(repo / ".ai" / "cap" / "skills", "cap")
    _settings(repo, {"cap@repo-plugins": True},
              {"repo-plugins": {"source": {"source": "directory", "path": "./.ai"}}})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=tmp_path / "none")
    assert len(srcs) == 1
    assert srcs[0].controlled is True
    assert srcs[0].source == ""             # in-repo -> fixable here
    assert srcs[0].origin == "repo-plugins/cap"


def test_assemble_github_marketplace_is_external_with_source(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mymarket", "ext", triggers=["do a thing"])
    _settings(repo, {"ext@mymarket": True},
              {"mymarket": {"source": {"source": "github", "repo": "owner/mrepo"}}})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)
    assert len(srcs) == 1
    assert srcs[0].controlled is False
    assert srcs[0].source == "https://github.com/owner/mrepo"


def test_assemble_source_falls_back_to_plugin_manifest(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    # No marketplace source entry -> read repository from the plugin manifest.
    _installed_plugin(installed, "mkt", "ext", repository="https://github.com/o/r")
    _settings(repo, {"ext@mkt": True}, {})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)
    assert len(srcs) == 1 and srcs[0].source == "https://github.com/o/r"


def test_assemble_skips_disabled_and_missing_footprint(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mkt", "present")
    _settings(repo, {"present@mkt": True, "absent@mkt": True, "off@mkt": False},
              {"mkt": {"source": {"source": "github", "repo": "o/r"}}})
    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)
    assert [s.origin for s in srcs] == ["mkt/present"]  # absent (no footprint) + off skipped


def test_assemble_includes_hook_only_plugin(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    plugin = installed / "mkt" / "hooks-only"
    plugin.mkdir(parents=True)
    (plugin / "hooks.json").write_text(
        json.dumps({"hooks": {"sessionStart": []}}), encoding="utf-8"
    )
    _settings(repo, {"hooks-only@mkt": True}, {})

    srcs = scan.assemble_enabled_plugins(repo, installed_root=installed)

    assert [source.origin for source in srcs] == ["mkt/hooks-only"]


def test_raw_plugin_discovery_includes_agent_and_hook_only_plugins(
    tmp_path: Path,
):
    installed = tmp_path / "installed"
    agent_only = installed / "mkt" / "agent-only"
    _agent(agent_only / "agents", "worker", desc="Worker.")
    hook_only = installed / "mkt" / "hook-only"
    hook_only.mkdir(parents=True)
    (hook_only / "hooks.json").write_text(
        json.dumps({"hooks": {"sessionStart": []}}), encoding="utf-8"
    )
    empty = installed / "mkt" / "empty"
    empty.mkdir(parents=True)

    sources = scan._sources_from_raw_dir(installed)

    assert [source.origin for source in sources] == [
        "mkt/agent-only",
        "mkt/hook-only",
    ]


# ---------------------------------------------------------------------------
# collision annotation for external plugins
# ---------------------------------------------------------------------------

def _run_with_sources(repo: Path, sources: list) -> scan.Report:
    return scan.run(repo, sources)


def test_local_vs_external_collision_is_annotated(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".github" / "skills").mkdir(parents=True)
    _skill(repo / ".github" / "skills", "mine", triggers=["shared phrase"])
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mkt", "ext", triggers=["shared phrase"],
                      repository="https://github.com/o/r")
    ext = scan.PluginSource(
        skills_root=installed / "mkt" / "ext" / "skills",
        origin="mkt/ext", controlled=False, source="https://github.com/o/r")
    report = _run_with_sources(repo, [ext])
    coll = [f for f in report.findings if f.check == "trigger-collision"]
    assert len(coll) == 1
    m = coll[0].message
    assert "shared phrase" in m
    assert "OUTSIDE this repo's control" in m
    assert "https://github.com/o/r" in m
    assert "contributing-to-copilot-extensions" in m  # the bridge pointer


def test_controlled_plugin_gets_full_checks(tmp_path: Path):
    """An in-repo (controlled) plugin is checked like an owned skill -- a
    name/folder mismatch is a BLOCKING finding, not reference-only silence."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ai = tmp_path / "ai"
    (ai / "cap" / "skills").mkdir(parents=True)
    _skill(ai / "cap" / "skills", "wrongname", folder="cap")  # name != folder
    ctrl = scan.PluginSource(skills_root=ai / "cap" / "skills",
                             origin="repo-plugins/cap", controlled=True)
    report = _run_with_sources(repo, [ctrl])
    assert any(f.check == "name-folder-match" and f.severity == scan.BLOCKING
               for f in report.findings)


def test_external_plugin_is_reference_only(tmp_path: Path):
    """An external plugin's own frontmatter problems are NOT flagged (we don't
    own it) -- only its triggers participate in collision detection."""
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    # name != folder in the external plugin -> must NOT raise a finding.
    pdir = installed / "mkt" / "ext"
    (pdir / "skills" / "cap").mkdir(parents=True)
    (pdir / "skills" / "cap" / "SKILL.md").write_text(
        "---\nname: mismatch\ndescription: x\n---\n", encoding="utf-8")
    ext = scan.PluginSource(skills_root=pdir / "skills", origin="mkt/ext",
                            controlled=False, source="")
    report = _run_with_sources(repo, [ext])
    assert not any(f.check == "name-folder-match" for f in report.findings)


def test_controlled_plugin_agents_get_frontmatter_and_recursion_checks(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = repo / ".ai" / "cap"
    agents = plugin / "agents"
    agents.mkdir(parents=True)
    (agents / "missing.agent.md").write_text(
        "# Missing frontmatter\n", encoding="utf-8"
    )
    (agents / "recursive.agent.md").write_text(
        "---\ndescription: Recursive.\nmcp-servers:\n  tool: {}\n---\n\n"
        "# Recursive\n",
        encoding="utf-8",
    )
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="repo-plugins/cap",
        controlled=True,
    )

    report = scan.run(repo, [source])

    assert any(f.check == "agent-frontmatter" for f in report.findings)
    assert sum(f.check == "anti-recursion" for f in report.findings) == 2


def test_external_plugin_agents_remain_reference_only(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "installed" / "mkt" / "external"
    agents = plugin / "agents"
    agents.mkdir(parents=True)
    (agents / "invalid.agent.md").write_text(
        "# No frontmatter\n", encoding="utf-8"
    )
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="mkt/external",
        controlled=False,
    )

    report = scan.run(repo, [source])

    assert not any(f.check in {"agent-frontmatter", "anti-recursion"}
                   for f in report.findings)


def test_purely_local_collision_has_no_external_annotation(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".github" / "skills").mkdir(parents=True)
    _skill(repo / ".github" / "skills", "a", triggers=["dup"])
    _skill(repo / ".github" / "skills", "b", triggers=["dup"])
    report = _run_with_sources(repo, [])
    coll = [f for f in report.findings if f.check == "trigger-collision"]
    assert len(coll) == 1
    assert "OUTSIDE this repo's control" not in coll[0].message


# ---------------------------------------------------------------------------
# context-budget inventory
# ---------------------------------------------------------------------------

def _agent(dir_: Path, name: str, *, desc: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{name}.agent.md").write_text(
        f"---\ndescription: {desc}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_context_budget_counts_static_custom_and_metadata(
    tmp_path: Path, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("repo guidance\n", encoding="utf-8")
    nested = repo / "pkg"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("nested rule\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("claude guidance\n", encoding="utf-8")
    (repo / "GEMINI.md").write_text("gemini guidance\n", encoding="utf-8")
    custom_one = tmp_path / "instructions-one"
    custom_one.mkdir()
    (custom_one / "operator.instructions.md").write_text(
        "operator policy one\n", encoding="utf-8")
    (custom_one / "unrelated.md").write_text(
        "not an instruction payload\n", encoding="utf-8")
    custom_two = tmp_path / "instructions-two"
    custom_two.mkdir()
    (custom_two / "machine.instructions.md").write_text(
        "operator policy two\n", encoding="utf-8"
    )
    monkeypatch.setenv(
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
        f"{custom_one}{os.pathsep}{custom_two}",
    )
    home = tmp_path / "home"
    personal = home / ".copilot"
    personal.mkdir(parents=True)
    (personal / "copilot-instructions.md").write_text(
        "personal policy\n", encoding="utf-8"
    )

    _skill(repo / ".github" / "skills", "local", desc="Local description.")
    _agent(repo / ".github" / "agents", "local-agent",
           desc="Agent description.")

    plugin = tmp_path / "plugin"
    _skill(plugin / "skills", "enabled", desc="Enabled description.")
    _agent(plugin / "agents", "enabled-agent", desc="Enabled agent.")
    source = scan.PluginSource(
        skills_root=plugin / "skills", origin="market/enabled",
    )

    budget = scan.build_context_budget(repo, [source], home=home)
    static = budget["static_instruction_payloads"]
    assert len(static["repository_always_loaded_files"]) == 3
    assert len(static["repository_conditional_agents_files"]) == 1
    assert len(static["personal_copilot_files"]) == 1
    assert len(static["custom_instruction_dir_files"]) == 2
    assert static["totals"]["characters"] == (
        len("repo guidance\n")
        + len("claude guidance\n")
        + len("gemini guidance\n")
        + len("nested rule\n")
        + len("personal policy\n")
        + len("operator policy one\n")
        + len("operator policy two\n")
    )
    assert static["personal_copilot_files"][0]["path"] == (
        "<personal-copilot>/copilot-instructions.md"
    )
    assert {
        entry["path"] for entry in static["custom_instruction_dir_files"]
    } == {
        "<custom-instructions-1>/operator.instructions.md",
        "<custom-instructions-2>/machine.instructions.md",
    }
    metadata = budget["metadata_upper_bounds"]
    assert len(metadata["files"]) == 4
    assert any(
        entry["path"] == "<plugin:market/enabled>/skills/enabled/SKILL.md"
        for entry in metadata["files"]
    )
    assert metadata["totals"]["estimated_tokens"] > 0
    assert budget["token_estimate"] == {
        "heuristic": "ceil(unicode_characters / 4)",
        "characters_per_token": 4,
    }


def test_custom_instruction_dirs_accept_comma_separator(
    tmp_path: Path, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "first.instructions.md").write_text("first", encoding="utf-8")
    (second / "second.instructions.md").write_text("second", encoding="utf-8")
    monkeypatch.setenv(
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS", f"{first},{second}"
    )

    budget = scan.build_context_budget(
        repo, home=tmp_path / "empty-home"
    )

    files = budget["static_instruction_payloads"][
        "custom_instruction_dir_files"
    ]
    assert [entry["path"] for entry in files] == [
        "<custom-instructions-1>/first.instructions.md",
        "<custom-instructions-2>/second.instructions.md",
    ]


def test_repo_instruction_walk_prunes_excluded_trees(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    nested = repo / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("included", encoding="utf-8")
    excluded = repo / "node_modules" / "package"
    excluded.mkdir(parents=True)
    (excluded / "AGENTS.md").write_text("excluded", encoding="utf-8")

    _, conditional = scan._repo_instruction_files(repo)

    assert conditional == {nested / "AGENTS.md"}


def test_context_budget_splits_hooks_without_executing(
    tmp_path: Path, capsys,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    user_hooks = home / ".copilot" / "hooks"
    user_hooks.mkdir(parents=True)
    plugin = tmp_path / "plugin"
    (plugin / "skills").mkdir(parents=True)
    marker = tmp_path / "hook-ran"
    hook = plugin / "hooks.json"
    hook.write_text(json.dumps({
        "version": 1,
        "hooks": {
            "sessionStart": [{
                "type": "command",
                "bash": f"touch {marker}",
                "powershell": f"New-Item '{marker}'",
            }, {
                "type": "prompt",
                "prompt": "Review the current session.",
            }],
            "preToolUse": [{
                "type": "command",
                "bash": f"touch {marker}",
            }],
        },
    }), encoding="utf-8")
    (user_hooks / "personal.json").write_text(json.dumps({
        "hooks": {
            "notification": [{"type": "command", "bash": f"touch {marker}"}],
        },
    }), encoding="utf-8")
    user_settings = home / ".copilot" / "settings.json"
    user_settings.write_text(json.dumps({
        "hooks": {
            "agentStop": [{"type": "command", "bash": f"touch {marker}"}],
        },
    }), encoding="utf-8")
    repo_settings = repo / ".github" / "copilot" / "settings.json"
    repo_settings.parent.mkdir(parents=True)
    repo_settings.write_text(json.dumps({
        "hooks": {
            "postToolUseFailure": [
                {"type": "command", "bash": f"touch {marker}"}
            ],
        },
    }), encoding="utf-8")
    source = scan.PluginSource(
        skills_root=plugin / "skills", origin="market/plugin",
    )

    budget = scan.build_context_budget(repo, [source], home=home)
    hooks = budget["hook_registrations"]
    context_hooks = hooks["additional_context_capable"]
    prompt_hooks = hooks["prompt_hooks"]
    other_hooks = hooks["not_additional_context_capable"]
    assert context_hooks["count"] == 3
    assert context_hooks["emitted_payload_size"] == "unknown"
    assert {
        (entry["path"], entry["event"]) for entry in context_hooks["registrations"]
    } == {
        ("<plugin:market/plugin>/hooks.json", "sessionStart"),
        ("<personal-copilot>/hooks/personal.json", "notification"),
        (".github/copilot/settings.json", "postToolUseFailure"),
    }
    assert prompt_hooks == {
        "count": 1,
        "payload_size": "unknown",
        "additional_context": False,
        "registrations": [{
            "path": "<plugin:market/plugin>/hooks.json",
            "source": "plugin:market/plugin",
            "event": "sessionStart",
            "index": 1,
            "type": "prompt",
            "payload_size": "unknown",
        }],
    }
    assert other_hooks["count"] == 2
    assert {
        (entry["path"], entry["event"]) for entry in other_hooks["registrations"]
    } == {
        ("<plugin:market/plugin>/hooks.json", "preToolUse"),
        ("<personal-copilot>/settings.json", "agentStop"),
    }
    assert not marker.exists()

    scan._print_context_budget(budget)
    output = capsys.readouterr().out
    assert "additionalContext hooks" in output
    assert "Prompt hooks" in output
    assert "payload size unknown (not additionalContext)" in output


def test_json_context_budget_shape(tmp_path: Path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("abcde", encoding="utf-8")
    monkeypatch.delenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", raising=False)
    monkeypatch.setattr(scan.Path, "home", lambda: tmp_path / "empty-home")

    assert scan.main([str(repo), "--json", "--context-budget"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["context_budget"]) == {
        "token_estimate",
        "static_instruction_payloads",
        "metadata_upper_bounds",
        "hook_registrations",
        "known_totals",
    }
    assert set(payload["context_budget"]["static_instruction_payloads"]) == {
        "totals",
        "repository_always_loaded_files",
        "repository_conditional_agents_files",
        "personal_copilot_files",
        "custom_instruction_dir_files",
    }
    totals = payload["context_budget"]["static_instruction_payloads"]["totals"]
    assert totals == {
        "characters": 5,
        "bytes": 5,
        "words": 1,
        "estimated_tokens": 2,
    }
