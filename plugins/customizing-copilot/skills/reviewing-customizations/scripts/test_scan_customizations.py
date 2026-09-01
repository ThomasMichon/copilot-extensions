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

import pytest

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


def _settings(
    repo: Path,
    enabled: dict,
    marketplaces: dict,
    *,
    aggregation: object | None = None,
) -> None:
    p = repo / ".github" / "copilot" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabledPlugins": enabled, "extraKnownMarketplaces": marketplaces,
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    if aggregation is not None:
        config = repo / ".context-injection" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(aggregation, dict):
            config.write_text(f"{aggregation!r}\n", encoding="utf-8")
        else:
            lines: list[str] = []
            for key, value in aggregation.items():
                if isinstance(value, dict):
                    lines.append(f"{key}:")
                    lines.extend(
                        f"  {nested_key}: {nested_value}"
                        for nested_key, nested_value in value.items()
                    )
                else:
                    lines.append(f"{key}: {value}")
            config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _marketplace(
    root: Path,
    name: str,
    *,
    plugin_root: str | None = None,
    entries: list[dict],
) -> None:
    path = root / ".github" / "plugin" / "marketplace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": name, "plugins": entries}
    if plugin_root is not None:
        manifest["metadata"] = {"pluginRoot": plugin_root}
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _installed_plugin(root: Path, mkt: str, name: str, *,
                      triggers: list[str] | None = None,
                      repository: str | None = None,
                      version: str = "1.2.3") -> None:
    pdir = root / mkt / name
    (pdir / "skills").mkdir(parents=True, exist_ok=True)
    _skill(pdir / "skills", name, triggers=triggers)
    manifest = {"name": name, "version": version}
    if repository:
        manifest["repository"] = repository
    (pdir / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _session_plugin(
    root: Path,
    marketplace: str,
    name: str,
    *,
    contributors: list[dict] | None = None,
    declaration: str = "complete",
    session_start: bool = True,
    create_commands: bool = True,
    side_effects: str | None = None,
    context_behavior: str | None = None,
) -> Path:
    plugin = root / marketplace / name
    plugin.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "hooks": "hooks.json",
    }
    if declaration != "missing":
        manifest["sessionContext"] = "session-context.json"
    (plugin / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (plugin / "hooks.json").write_text(
        json.dumps({
            "version": 1,
            "hooks": {
                "sessionStart": (
                    [{
                        "type": "command",
                        "bash": "do-not-report-this-command",
                        "powershell": "do-not-report-this-command",
                    }]
                    if session_start
                    else []
                ),
            },
        }),
        encoding="utf-8",
    )
    if declaration != "missing":
        if side_effects is None:
            side_effects = (
                "none" if contributors else "restart-safe-idempotent"
            )
        if context_behavior is None:
            context_behavior = "authority-aware" if contributors else "none"
        payload = {
            "schema": scan.SESSION_CONTEXT_SCHEMA,
            "version": scan.SESSION_CONTEXT_VERSION,
            "complete": declaration == "complete",
            "sessionStart": {
                "sideEffects": side_effects,
                "context": context_behavior,
            },
            "contributors": contributors or [],
        }
        (plugin / "session-context.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        if create_commands:
            for contributor in contributors or []:
                for platform in ("bash", "powershell"):
                    argv = contributor.get(platform)
                    if not isinstance(argv, list) or not argv:
                        continue
                    relative = Path(str(argv[0]))
                    if relative.is_absolute() or ".." in relative.parts:
                        continue
                    command = plugin / relative
                    command.parent.mkdir(parents=True, exist_ok=True)
                    command.write_text("", encoding="utf-8")
    return plugin


def _pure_contributor(name: str = "ambient") -> dict:
    return {
        "id": name,
        "pure": True,
        "bash": ["scripts/emit-context.sh"],
        "powershell": ["scripts/emit-context.ps1"],
    }


def _adoption(
    authority: str = "context-injection@copilot-extensions",
) -> dict:
    return {
        "schema": scan.SESSION_CONTEXT_ADOPTION_SCHEMA,
        "version": 1,
        "authority": authority,
        "engine": {
            "schema": scan.SESSION_CONTEXT_ENGINE_SCHEMA,
            "version": scan.SESSION_CONTEXT_ENGINE_VERSION,
        },
    }


def _aggregate_authority(
    root: Path,
    marketplace: str = "copilot-extensions",
) -> Path:
    authority = _session_plugin(
        root,
        marketplace,
        "context-injection",
        side_effects="none",
        context_behavior="aggregate-authority",
    )
    manifest_path = authority / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sessionContextEngine"] = "engine.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (authority / "engine.json").write_text(
        json.dumps({
            "schema": scan.SESSION_CONTEXT_ENGINE_SCHEMA,
            "version": scan.SESSION_CONTEXT_ENGINE_VERSION,
        }),
        encoding="utf-8",
    )
    return authority


def _isolate_user_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scan.Path, "home", lambda: tmp_path / "empty-home")


def _scan_contributor_with_authority(
    repo: Path,
    installed: Path,
) -> tuple[dict, scan.Report]:
    _settings(
        repo,
        {
            "ambient-policy@copilot-extensions": True,
            "context-injection@copilot-extensions": True,
        },
        {},
        aggregation=_adoption(),
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()
    return scan.scan_session_context(repo, sources, report), report


# ---------------------------------------------------------------------------
# assemble_enabled_plugins
# ---------------------------------------------------------------------------

def test_assemble_directory_marketplace_is_controlled(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".ai" / "cap" / "skills").mkdir(parents=True)
    _skill(repo / ".ai" / "cap" / "skills", "cap")
    (repo / ".ai" / "cap" / "plugin.json").write_text(
        json.dumps({"name": "cap", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _marketplace(
        repo / ".ai",
        "repo-plugins",
        entries=[{"name": "cap", "source": "cap"}],
    )
    _settings(repo, {"cap@repo-plugins": True},
              {"repo-plugins": {"source": {"source": "directory", "path": "./.ai"}}})
    srcs = scan.assemble_enabled_plugins(
        repo,
        installed_root=tmp_path / "none",
        home=tmp_path / "home",
    )
    assert len(srcs) == 1
    assert srcs[0].controlled is True
    assert srcs[0].source == ""             # in-repo -> fixable here
    assert srcs[0].origin == "repo-plugins/cap"


def test_directory_marketplace_honors_plugin_root_and_entry_source(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    marketplace = repo / "local-marketplace"
    plugin = marketplace / "payloads" / "nested" / "capability"
    (plugin / "skills").mkdir(parents=True)
    _skill(plugin / "skills", "cap")
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "cap", "version": "2.0.0"}),
        encoding="utf-8",
    )
    _marketplace(
        marketplace,
        "repo-plugins",
        plugin_root="payloads",
        entries=[{"name": "cap", "source": "nested/capability"}],
    )
    _settings(
        repo,
        {"cap@repo-plugins": True},
        {
            "repo-plugins": {
                "source": {
                    "source": "directory",
                    "path": "./local-marketplace",
                },
            },
        },
    )

    sources = scan.assemble_enabled_plugins(
        repo,
        installed_root=tmp_path / "none",
        home=tmp_path / "home",
    )

    assert len(sources) == 1
    assert sources[0].payload_root == plugin.resolve()
    assert sources[0].controlled is True
    assert sources[0].version == "2.0.0"


@pytest.mark.parametrize(
    ("entry_source", "manifest_name"),
    [
        ("../outside", "cap"),
        ("inside", "different-plugin"),
    ],
)
def test_directory_marketplace_rejects_escape_or_wrong_plugin_identity(
    tmp_path: Path,
    entry_source: str,
    manifest_name: str,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    marketplace = repo / "local-marketplace"
    plugin = (
        repo / "outside"
        if entry_source == "../outside"
        else marketplace / "inside"
    )
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": manifest_name, "version": "1.0.0"}),
        encoding="utf-8",
    )
    _marketplace(
        marketplace,
        "repo-plugins",
        entries=[{"name": "cap", "source": entry_source}],
    )
    _settings(
        repo,
        {"cap@repo-plugins": True},
        {
            "repo-plugins": {
                "source": {
                    "source": "directory",
                    "path": "./local-marketplace",
                },
            },
        },
    )
    installed = tmp_path / "installed"
    _installed_plugin(installed, "repo-plugins", "cap")

    sources = scan.assemble_enabled_plugins(
        repo, installed_root=installed, home=tmp_path / "home"
    )

    assert sources[0].payload_root == installed / "repo-plugins" / "cap"
    assert sources[0].controlled is False


def test_local_settings_can_disable_base_plugin(tmp_path: Path):
    repo = tmp_path / "repo"
    plugin = repo / ".ai" / "cap"
    (plugin / "skills").mkdir(parents=True)
    _skill(plugin / "skills", "cap")
    settings = repo / ".github" / "copilot"
    settings.mkdir(parents=True)
    marketplace = {
        "repo-plugins": {
            "source": {"source": "directory", "path": "./.ai"}
        }
    }
    (settings / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"cap@repo-plugins": True},
        "extraKnownMarketplaces": marketplace,
    }), encoding="utf-8")
    (settings / "settings.local.json").write_text(json.dumps({
        "enabledPlugins": {"cap@repo-plugins": False},
    }), encoding="utf-8")

    assert scan.assemble_enabled_plugins(
        repo,
        installed_root=tmp_path / "none",
        home=tmp_path / "home",
    ) == []


def test_assemble_github_marketplace_is_external_with_source(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mymarket", "ext", triggers=["do a thing"])
    _settings(repo, {"ext@mymarket": True},
              {"mymarket": {"source": {"source": "github", "repo": "owner/mrepo"}}})
    srcs = scan.assemble_enabled_plugins(
        repo, installed_root=installed, home=tmp_path / "home"
    )
    assert len(srcs) == 1
    assert srcs[0].controlled is False
    assert srcs[0].source == "https://github.com/owner/mrepo"
    assert srcs[0].version == "1.2.3"


def test_assemble_source_falls_back_to_plugin_manifest(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    # No marketplace source entry -> read repository from the plugin manifest.
    _installed_plugin(installed, "mkt", "ext", repository="https://github.com/o/r")
    _settings(repo, {"ext@mkt": True}, {})
    srcs = scan.assemble_enabled_plugins(
        repo, installed_root=installed, home=tmp_path / "home"
    )
    assert len(srcs) == 1 and srcs[0].source == "https://github.com/o/r"


def test_assemble_skips_disabled_and_missing_footprint(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _installed_plugin(installed, "mkt", "present")
    _settings(repo, {"present@mkt": True, "absent@mkt": True, "off@mkt": False},
              {"mkt": {"source": {"source": "github", "repo": "o/r"}}})
    srcs = scan.assemble_enabled_plugins(
        repo, installed_root=installed, home=tmp_path / "home"
    )
    assert [s.origin for s in srcs] == ["mkt/absent", "mkt/present"]
    assert [s.payload_root.is_dir() for s in srcs] == [False, True]


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

    srcs = scan.assemble_enabled_plugins(
        repo, installed_root=installed, home=tmp_path / "home"
    )

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


def test_suite_source_skill_supersedes_installed_copy(tmp_path: Path):
    repo = tmp_path / "repo"
    suite_skills = repo / "plugins" / "suite-skill" / "skills"
    _skill(suite_skills, "worker", triggers=["shared phrase"])
    installed = tmp_path / "installed" / "suite-skill"
    _skill(
        installed / "skills", "worker", triggers=["shared phrase"]
    )
    source = scan.PluginSource(
        skills_root=installed / "skills",
        origin="copilot-extensions/suite-skill",
        controlled=False,
        version="1.2.3",
    )

    report = _run_with_sources(repo, [source])

    assert not any(
        f.check == "trigger-collision" for f in report.findings
    )


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
    assert any(f.check == "mcp-readiness" for f in report.findings)
    assert any(f.check == "anti-recursion" for f in report.findings)


def test_task_capable_project_agent_requires_self_guard(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "worker.agent.md").write_text(
        "---\ndescription: Worker.\ntools: ['*']\n---\n\n# Worker\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    finding = next(f for f in report.findings if f.check == "anti-recursion")
    assert finding.severity == scan.BLOCKING
    assert "`worker` agent" in finding.message


def test_task_disabled_agent_is_exempt_from_self_guard(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "reader.agent.md").write_text(
        "---\n"
        "description: Reader.\n"
        "tools:\n"
        "  - read\n"
        "  - search\n"
        "---\n\n"
        "# Reader\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert not any(f.check == "anti-recursion" for f in report.findings)


def test_scoped_wildcard_does_not_grant_task(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "reader.agent.md").write_text(
        "---\n"
        "description: Reader.\n"
        "tools: ['read', 'service/*']\n"
        "---\n\n"
        "# Reader\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert not any(f.check == "anti-recursion" for f in report.findings)


def test_nested_mcp_tools_do_not_imply_task_is_disabled(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "mcp-servers:\n"
        "  service:\n"
        "    tools: ['*']\n"
        "---\n\n"
        "## MCP Readiness\n"
        "Probe service_health.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert any(f.check == "anti-recursion" for f in report.findings)


def test_coordinator_can_delegate_other_types_with_self_guard(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "coordinator.agent.md").write_text(
        "---\ndescription: Coordinator.\ntools: ['*']\n---\n\n"
        "Delegate bounded evidence work to research agents when authorized.\n"
        "Do NOT use the task tool to spawn another `coordinator` agent.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert not any(f.check == "anti-recursion" for f in report.findings)


def test_claude_project_agents_receive_equivalent_checks(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "worker.agent.md").write_text(
        "---\ndescription: Worker.\n---\n\n# Worker\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert any(
        f.check == "anti-recursion"
        and f.severity == scan.BLOCKING
        and f.path.endswith("worker.agent.md")
        for f in report.findings
    )


def test_suite_plugin_agent_is_blocking_from_editable_source(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / "plugins" / "suite-agent" / "agents"
    agents.mkdir(parents=True)
    (agents / "worker.agent.md").write_text(
        "---\ndescription: Worker.\ntools: ['*']\n---\n\n# Worker\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    finding = next(f for f in report.findings if f.check == "anti-recursion")
    assert finding.severity == scan.BLOCKING


def test_suite_source_agent_supersedes_installed_copy(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / "plugins" / "suite-agent" / "agents"
    agents.mkdir(parents=True)
    content = (
        "---\ndescription: Worker.\ntools: ['*']\n---\n\n# Worker\n"
    )
    (agents / "worker.agent.md").write_text(content, encoding="utf-8")
    installed = tmp_path / "installed" / "suite-agent"
    installed_agents = installed / "agents"
    installed_agents.mkdir(parents=True)
    (installed_agents / "worker.agent.md").write_text(
        content, encoding="utf-8"
    )
    source = scan.PluginSource(
        skills_root=installed / "skills",
        origin="copilot-extensions/suite-agent",
        controlled=False,
        version="1.2.3",
    )

    report = scan.run(repo, [source])

    findings = [
        f for f in report.findings if f.check == "anti-recursion"
    ]
    assert len(findings) == 1
    assert findings[0].severity == scan.BLOCKING
    assert findings[0].path.endswith("worker.agent.md")
    assert not findings[0].path.startswith("<plugin:")


def test_agent_mcp_agent_requires_materialized_fallback(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "mcp-servers:\n"
        "  service:\n"
        "    command: agent-mcp # cross-platform\n"
        "---\n\n"
        "## MCP Readiness\n"
        "Probe service_health.\n"
        "Do NOT use the task tool to spawn another service agent.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert any(f.check == "mcp-fallback" for f in report.findings)


def test_agent_mcp_agent_with_fallback_passes(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "mcp-servers:\n"
        "  service:\n"
        "    command: agent-mcp\n"
        "---\n\n"
        "## MCP Readiness\n"
        "Probe service_health. On catalog failure use the materialized fleet.\n"
        "Do NOT use the task tool to spawn another service agent.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert not any(
        f.check in {"anti-recursion", "mcp-fallback"} for f in report.findings
    )


def test_agent_mcp_agent_rejects_negated_fallback(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "mcp-servers:\n"
        "  service:\n"
        "    command: agent-mcp\n"
        "---\n\n"
        "## MCP Readiness\n"
        "Probe service_health. Do not use a materialized fallback.\n"
        "Do NOT use the task tool to spawn another service agent.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert any(f.check == "mcp-fallback" for f in report.findings)


def test_agent_mcp_agent_accepts_conditional_auth_opt_out(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "mcp-servers:\n"
        "  service:\n"
        "    command: agent-mcp\n"
        "---\n\n"
        "## MCP Readiness\n"
        "Probe service_health.\n"
        "Materialized CLI fallback: disabled because authorization uses a "
        "conditional gate.\n"
        "Do NOT use the task tool to spawn another service agent.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert not any(f.check == "mcp-fallback" for f in report.findings)


def test_agent_mcp_agent_accepts_scoped_auth_warning(tmp_path: Path):
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "mcp-servers:\n"
        "  service:\n"
        "    command: agent-mcp\n"
        "---\n\n"
        "## MCP Readiness\n"
        "On catalog failure use the materialized fleet. Never use a "
        "materialized fallback after authentication fails.\n"
        "Do NOT use the task tool to spawn another service agent.\n",
        encoding="utf-8",
    )

    report = scan.run(repo)

    assert not any(f.check == "mcp-fallback" for f in report.findings)


def test_fallback_parser_rejects_common_negations():
    for phrase in (
        "You must not use the materialized fleet.",
        "You should not use the materialized fleet.",
        "You cannot use the materialized fleet.",
        "You may not fall back to the materialized fleet.",
        "Use no materialized fallback.",
        "Use neither the materialized fleet nor any CLI fallback.",
    ):
        assert not scan.has_mcp_fallback(phrase)


def test_external_plugin_agent_guard_is_origin_version_advisory(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "installed" / "mkt" / "external"
    agents = plugin / "agents"
    agents.mkdir(parents=True)
    (agents / "worker.agent.md").write_text(
        "---\ndescription: Worker.\ntools: ['*']\n---\n\n# Worker\n",
        encoding="utf-8",
    )
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="mkt/external",
        controlled=False,
        source="https://github.com/example/external",
        version="4.5.6",
    )

    report = scan.run(repo, [source])

    finding = next(f for f in report.findings if f.check == "anti-recursion")
    assert finding.severity == scan.WARNING
    assert finding.path == (
        "<plugin:mkt/external@4.5.6>/agents/worker.agent.md"
    )
    assert "`mkt/external@4.5.6`" in finding.message
    assert "https://github.com/example/external" in finding.message
    assert "cannot edit its installed payload" in finding.message


def test_external_plugin_agent_mcp_checks_are_advisory(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "installed" / "mkt" / "external"
    agents = plugin / "agents"
    agents.mkdir(parents=True)
    (agents / "service.agent.md").write_text(
        "---\n"
        "description: Service.\n"
        "tools: ['read']\n"
        "mcp-servers:\n"
        "  service:\n"
        "    command: agent-mcp\n"
        "---\n\n"
        "# Service\n",
        encoding="utf-8",
    )
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="mkt/external",
        controlled=False,
        source="https://github.com/example/external",
        version="4.5.6",
    )

    report = scan.run(repo, [source])

    findings = {
        f.check: f for f in report.findings
        if f.check in {"mcp-readiness", "mcp-fallback"}
    }
    assert set(findings) == {"mcp-readiness", "mcp-fallback"}
    assert all(f.severity == scan.WARNING for f in findings.values())


def test_controlled_plugin_text_files_get_secret_checks(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = repo / "local-plugins" / "cap"
    plugin.mkdir(parents=True)
    secret = "abcdefghijklmnop"
    (plugin / "config.yaml").write_text(
        f"api_key: {secret}\n", encoding="utf-8"
    )
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="repo-plugins/cap",
        controlled=True,
    )

    report = scan.run(repo, [source])

    findings = [f for f in report.findings if f.check == "secret"]
    assert len(findings) == 1
    assert secret not in findings[0].message


def test_manifest_declared_hook_path_is_inventoried(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "plugin"
    custom_hook = plugin / "hooks" / "session-start.json"
    custom_hook.parent.mkdir(parents=True)
    custom_hook.write_text(json.dumps({
        "hooks": {
            "sessionStart": [{"type": "command", "bash": "true"}],
        },
    }), encoding="utf-8")
    (plugin / "plugin.json").write_text(json.dumps({
        "name": "custom-hook",
        "hooks": "hooks/session-start.json",
    }), encoding="utf-8")
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="market/custom-hook",
    )

    assert scan._has_reviewable_payload(plugin)
    budget = scan.build_context_budget(repo, [source], home=tmp_path / "home")

    registrations = budget["hook_registrations"][
        "additional_context_capable"
    ]["registrations"]
    assert registrations[0]["path"] == (
        "<plugin:market/custom-hook>/hooks/session-start.json"
    )


def test_native_manifest_hook_path_wins_over_claude_fallback(tmp_path: Path):
    plugin = tmp_path / "plugin"
    native_hook = plugin / "hooks" / "native.json"
    fallback_hook = plugin / "hooks" / "fallback.json"
    native_hook.parent.mkdir(parents=True)
    native_hook.write_text("{}", encoding="utf-8")
    fallback_hook.write_text("{}", encoding="utf-8")
    (plugin / "plugin.json").write_text(json.dumps({
        "name": "native",
        "hooks": "hooks/native.json",
    }), encoding="utf-8")
    fallback_manifest = plugin / ".claude-plugin" / "plugin.json"
    fallback_manifest.parent.mkdir()
    fallback_manifest.write_text(json.dumps({
        "name": "fallback",
        "hooks": "hooks/fallback.json",
    }), encoding="utf-8")

    assert scan._plugin_hook_files(plugin) == {native_hook}


def test_manifest_hook_path_cannot_escape_plugin(tmp_path: Path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (plugin / "plugin.json").write_text(json.dumps({
        "name": "escaped",
        "hooks": "../outside.json",
    }), encoding="utf-8")

    assert scan._plugin_hook_files(plugin) == set()


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
# session-start context composition
# ---------------------------------------------------------------------------

def test_same_named_external_plugin_does_not_use_editable_suite_source(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _session_plugin(
        repo,
        "plugins",
        "shared-name",
        contributors=[_pure_contributor()],
    )
    installed = tmp_path / "installed"
    _session_plugin(
        installed,
        "external-market",
        "shared-name",
        declaration="missing",
    )
    _settings(repo, {"shared-name@external-market": True}, {})
    sources = scan.assemble_enabled_plugins(
        repo, installed_root=installed
    )
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["plugins"] == [{
        "identity": "external-market/shared-name",
        "role": "legacy-direct-or-unknown",
        "session_start": "yes",
        "declaration": "missing",
        "possible_non_empty": "unknown",
    }]


def test_suite_identity_uses_editable_plugin_source(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    editable = _session_plugin(
        repo,
        "plugins",
        "shared-name",
        contributors=[_pure_contributor()],
    )
    installed = tmp_path / "installed"
    _session_plugin(
        installed,
        "copilot-extensions",
        "shared-name",
        declaration="missing",
    )
    _settings(repo, {"shared-name@copilot-extensions": True}, {})
    sources = scan.assemble_enabled_plugins(
        repo, installed_root=installed
    )

    assert scan._editable_plugin_footprint(repo, sources[0]) == editable


def test_session_context_rejects_unadopted_aggregate_authority(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _aggregate_authority(installed)
    _settings(
        repo,
        {"context-injection@copilot-extensions": True},
        {},
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["disposition"] == "unproven-aggregate-authority"
    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-authority"
        and finding.severity == scan.BLOCKING
        and "no complete repository adoption" in finding.message
        for finding in report.findings
    )


def test_session_context_accepts_exact_direct_authority(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    authority = _aggregate_authority(installed)
    contributor = _session_plugin(
        installed,
        "copilot-extensions",
        "ambient-policy",
        contributors=[_pure_contributor()],
    )
    side_effect = _session_plugin(
        installed,
        "copilot-extensions",
        "provider-registration",
    )
    mixed = _session_plugin(
        installed,
        "copilot-extensions",
        "mixed-capability",
        contributors=[_pure_contributor("mixed")],
        side_effects="restart-safe-idempotent",
        context_behavior="authority-aware",
    )
    _settings(
        repo,
        {
            "context-injection@copilot-extensions": True,
            "ambient-policy@copilot-extensions": True,
            "provider-registration@copilot-extensions": True,
            "mixed-capability@copilot-extensions": True,
        },
        {},
        aggregation=_adoption(),
    )
    sources = [
        scan.PluginSource(
            skills_root=authority / "skills",
            origin="copilot-extensions/context-injection",
        ),
        scan.PluginSource(
            skills_root=contributor / "skills",
            origin="copilot-extensions/ambient-policy",
        ),
        scan.PluginSource(
            skills_root=side_effect / "skills",
            origin="copilot-extensions/provider-registration",
        ),
        scan.PluginSource(
            skills_root=mixed / "skills",
            origin="copilot-extensions/mixed-capability",
        ),
    ]
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["authority_proven"] is True
    assert inventory["disposition"] == "repository-authority-proven"
    assert report.blocking == 0
    roles = {
        entry["identity"]: entry["role"]
        for entry in inventory["plugins"]
    }
    assert roles["copilot-extensions/context-injection"] == (
        "aggregate-authority"
    )
    assert roles["copilot-extensions/ambient-policy"] == (
        "complete-declared-contributor"
    )
    assert roles["copilot-extensions/provider-registration"] == (
        "complete-declared-side-effect-only"
    )
    assert roles["copilot-extensions/mixed-capability"] == (
        "complete-declared-contributor"
    )
    possible_non_empty = {
        entry["identity"]: entry["possible_non_empty"]
        for entry in inventory["plugins"]
    }
    assert possible_non_empty["copilot-extensions/context-injection"] == "yes"
    assert possible_non_empty["copilot-extensions/ambient-policy"] == "no"
    assert possible_non_empty["copilot-extensions/mixed-capability"] == "no"
    rendered = json.dumps(inventory)
    assert "do-not-report-this-command" not in rendered
    assert "scripts/emit-context" not in rendered


@pytest.mark.parametrize(
    "participant_case",
    [
        "unclassified",
        "incomplete",
        "direct",
        "side-effect-incomplete",
        "contributor-without-hook",
        "invalid-contributor-id",
    ],
)
def test_adopted_stack_rejects_unsafe_session_start_participant(
    tmp_path: Path,
    monkeypatch,
    participant_case: str,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    authority = _aggregate_authority(installed)
    participant_options: dict = {}
    if participant_case == "unclassified":
        participant_options["declaration"] = "missing"
    elif participant_case == "incomplete":
        participant_options["declaration"] = "incomplete"
    elif participant_case == "direct":
        participant_options.update(
            contributors=[_pure_contributor()],
            context_behavior="none",
        )
    elif participant_case == "contributor-without-hook":
        participant_options.update(
            contributors=[_pure_contributor()],
            session_start=False,
        )
    elif participant_case == "invalid-contributor-id":
        participant_options["contributors"] = [
            _pure_contributor("bad/id")
        ]
    else:
        participant_options.update(
            side_effects="none",
            context_behavior="none",
        )
    participant = _session_plugin(
        installed,
        "copilot-extensions",
        "participant",
        **participant_options,
    )
    _settings(repo, {}, {}, aggregation=_adoption())
    sources = [
        scan.PluginSource(
            skills_root=authority / "skills",
            origin="copilot-extensions/context-injection",
        ),
        scan.PluginSource(
            skills_root=participant / "skills",
            origin="copilot-extensions/participant",
        ),
    ]
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["authority_proven"] is False
    assert inventory["disposition"] == "unproven-aggregate-authority"
    assert any(
        finding.check == "session-context-authority"
        and "unclassified, incomplete, or not authority-aware"
        in finding.message
        for finding in report.findings
    )


def test_adopted_stack_rejects_malformed_repository_config(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _settings(repo, {}, {}, aggregation=True)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, [], report)

    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-authority"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


def test_adopted_stack_rejects_unknown_repository_config_key(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    adoption = _adoption()
    adoption["unexpected"] = "rejected"
    _settings(repo, {}, {}, aggregation=adoption)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, [], report)

    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-authority"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


def test_adopted_stack_rejects_previous_context_engine_version(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    adoption = _adoption()
    adoption["engine"]["version"] = scan.SESSION_CONTEXT_ENGINE_VERSION - 1
    _settings(repo, {}, {}, aggregation=adoption)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, [], report)

    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-authority"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


def test_adopted_stack_rejects_invalid_source_qualified_identity(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _settings(
        repo,
        {},
        {},
        aggregation=_adoption(
            authority="context-injection@repo_context"
        ),
    )
    report = scan.Report()

    inventory = scan.scan_session_context(repo, [], report)

    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-authority"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


@pytest.mark.parametrize("authority_first", [False, True])
def test_enabled_plugins_key_order_does_not_affect_context_authority(
    tmp_path: Path, monkeypatch, authority_first: bool,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _aggregate_authority(installed)
    _session_plugin(installed, "copilot-extensions", "ambient-policy")
    enabled_items = [
        ("ambient-policy@copilot-extensions", True),
        ("context-injection@copilot-extensions", True),
    ]
    if authority_first:
        enabled_items.reverse()
    _settings(
        repo,
        dict(enabled_items),
        {},
        aggregation=_adoption(),
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["authority_proven"] is True
    assert inventory["disposition"] == "repository-authority-proven"
    assert report.blocking == 0


def test_session_context_rejects_second_authority(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _aggregate_authority(installed)
    _aggregate_authority(installed, "other-market")
    _settings(
        repo,
        {
            "context-injection@copilot-extensions": True,
            "context-injection@other-market": True,
        },
        {},
        aggregation=_adoption(),
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["authority_proven"] is False
    assert inventory["disposition"] == "unproven-aggregate-authority"
    finding = next(
        finding for finding in report.findings
        if finding.check == "session-context-authority"
    )
    assert finding.severity == scan.BLOCKING
    assert "multiple aggregate authorities" in finding.message


def test_known_session_start_without_declaration_blocks_authority(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _session_plugin(
        installed,
        "copilot-extensions",
        "legacy-policy",
        declaration="missing",
    )
    _aggregate_authority(installed)
    _settings(
        repo,
        {
            "legacy-policy@copilot-extensions": True,
            "context-injection@copilot-extensions": True,
        },
        {},
        aggregation=_adoption(),
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-declaration"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )
    assert any(
        finding.check == "session-context-collision"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


def test_impure_contributor_is_incomplete_and_blocks_authority(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    contributor = _pure_contributor()
    contributor["pure"] = False
    _session_plugin(
        installed,
        "copilot-extensions",
        "ambient-policy",
        contributors=[contributor],
    )
    _aggregate_authority(installed)
    _settings(
        repo,
        {
            "ambient-policy@copilot-extensions": True,
            "context-injection@copilot-extensions": True,
        },
        {},
        aggregation=_adoption(),
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    ambient = next(
        entry for entry in inventory["plugins"]
        if entry["identity"] == "copilot-extensions/ambient-policy"
    )
    assert ambient["role"] == "legacy-direct-or-unknown"
    assert ambient["declaration"] == "incomplete"
    assert any(
        finding.check == "session-context-declaration"
        for finding in report.findings
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order", "first"),
        ("timeoutSeconds", 0),
        ("maxBytes", 65537),
    ],
)
def test_invalid_contributor_bounds_are_incomplete_and_block_authority(
    tmp_path: Path,
    monkeypatch,
    field: str,
    value: object,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    contributor = _pure_contributor()
    contributor[field] = value
    _session_plugin(
        installed,
        "copilot-extensions",
        "ambient-policy",
        contributors=[contributor],
    )
    _aggregate_authority(installed)

    inventory, report = _scan_contributor_with_authority(repo, installed)

    ambient = next(
        entry for entry in inventory["plugins"]
        if entry["identity"] == "copilot-extensions/ambient-policy"
    )
    assert ambient["declaration"] == "incomplete"
    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-declaration"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


@pytest.mark.parametrize("command_case", ["escape", "missing", "suffix"])
def test_invalid_contributor_command_is_incomplete_and_blocks_authority(
    tmp_path: Path,
    monkeypatch,
    command_case: str,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    contributor = _pure_contributor()
    if command_case == "escape":
        contributor["bash"] = ["../outside.sh"]
    elif command_case == "missing":
        contributor["bash"] = ["scripts/missing.sh"]
    else:
        contributor["bash"] = ["scripts/emit-context.txt"]
    plugin = _session_plugin(
        installed,
        "copilot-extensions",
        "ambient-policy",
        contributors=[contributor],
        create_commands=False,
    )
    powershell = plugin / "scripts" / "emit-context.ps1"
    powershell.parent.mkdir(parents=True)
    powershell.write_text("", encoding="utf-8")
    if command_case == "escape":
        (plugin.parent / "outside.sh").write_text("", encoding="utf-8")
    elif command_case == "suffix":
        (plugin / "scripts" / "emit-context.txt").write_text(
            "", encoding="utf-8"
        )
    _aggregate_authority(installed)

    inventory, report = _scan_contributor_with_authority(repo, installed)

    ambient = next(
        entry for entry in inventory["plugins"]
        if entry["identity"] == "copilot-extensions/ambient-policy"
    )
    assert ambient["declaration"] == "incomplete"
    assert inventory["authority_proven"] is False
    assert any(
        finding.check == "session-context-declaration"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


def test_unknown_external_plugin_output_is_warning_only(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _settings(repo, {"opaque@external-market": True}, {})
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["disposition"] == "indeterminate-stand-down"
    assert inventory["plugins"] == [{
        "identity": "external-market/opaque",
        "role": "legacy-direct-or-unknown",
        "session_start": "unknown",
        "declaration": "missing",
        "possible_non_empty": "unknown",
    }]
    assert report.blocking == 0
    warning = next(
        finding for finding in report.findings
        if finding.check == "session-context-unknown"
    )
    assert warning.severity == scan.WARNING


def test_unknown_external_keeps_adopted_stack_unproven(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _session_plugin(
        installed,
        "copilot-extensions",
        "ambient-policy",
        contributors=[_pure_contributor()],
    )
    _aggregate_authority(installed)
    _settings(
        repo,
        {
            "ambient-policy@copilot-extensions": True,
            "opaque@external-market": True,
            "context-injection@copilot-extensions": True,
        },
        {},
        aggregation=_adoption(),
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["disposition"] == "unproven-aggregate-authority"
    assert report.blocking > 0
    assert any(
        finding.check == "session-context-unknown"
        and finding.severity == scan.WARNING
        for finding in report.findings
    )
    assert any(
        finding.check == "session-context-authority"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


def test_multiple_direct_contributors_without_authority_block(
    tmp_path: Path, monkeypatch,
):
    _isolate_user_settings(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    for name in ("policy-a", "policy-b"):
        _session_plugin(
            installed,
            "copilot-extensions",
            name,
            contributors=[_pure_contributor(name)],
        )
    _settings(
        repo,
        {
            "policy-a@copilot-extensions": True,
            "policy-b@copilot-extensions": True,
        },
        {},
    )
    sources = scan.assemble_enabled_plugins(repo, installed_root=installed)
    report = scan.Report()

    inventory = scan.scan_session_context(repo, sources, report)

    assert inventory["disposition"] == "unsafe-multiple-output"
    assert any(
        finding.check == "session-context-collision"
        and finding.severity == scan.BLOCKING
        for finding in report.findings
    )


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
    (repo / "AGENTS.md").write_bytes(b"repo guidance\r\n")
    nested = repo / "pkg"
    nested.mkdir()
    (nested / "AGENTS.md").write_text(
        "nested rule\n", encoding="utf-8", newline=""
    )
    (nested / "CLAUDE.md").write_text(
        "nested claude\n", encoding="utf-8", newline=""
    )
    (nested / "GEMINI.md").write_text(
        "nested gemini\n", encoding="utf-8", newline=""
    )
    (repo / "CLAUDE.md").write_text(
        "claude guidance\n", encoding="utf-8", newline=""
    )
    (repo / "GEMINI.md").write_text(
        "gemini guidance\n", encoding="utf-8", newline=""
    )
    custom_one = tmp_path / "instructions-one"
    custom_one.mkdir()
    (custom_one / "operator.instructions.md").write_text(
        "operator policy one\n", encoding="utf-8", newline="")
    (custom_one / "unrelated.md").write_text(
        "not an instruction payload\n", encoding="utf-8")
    custom_two = tmp_path / "instructions-two"
    custom_two.mkdir()
    (custom_two / "machine.instructions.md").write_text(
        "operator policy two\n", encoding="utf-8", newline=""
    )
    monkeypatch.setenv(
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
        f"{custom_one}{os.pathsep}{custom_two}",
    )
    home = tmp_path / "home"
    personal = home / ".copilot"
    personal.mkdir(parents=True)
    (personal / "copilot-instructions.md").write_text(
        "personal policy\n", encoding="utf-8", newline=""
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
    assert len(static["repository_conditional_instruction_files"]) == 3
    assert len(static["personal_copilot_files"]) == 1
    assert len(static["custom_instruction_dir_files"]) == 2
    assert static["totals"]["characters"] == (
        len("repo guidance\r\n")
        + len("claude guidance\n")
        + len("gemini guidance\n")
        + len("nested rule\n")
        + len("nested claude\n")
        + len("nested gemini\n")
        + len("personal policy\n")
        + len("operator policy one\n")
        + len("operator policy two\n")
    )
    repo_agents = next(
        entry for entry in static["repository_always_loaded_files"]
        if entry["path"] == "AGENTS.md"
    )
    assert repo_agents["bytes"] == len(b"repo guidance\r\n")
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


def test_metadata_inventory_covers_supported_repo_surfaces(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _skill(repo / ".claude" / "skills", "claude-skill")
    _skill(repo / ".agents" / "skills", "agents-skill")
    _agent(
        repo / ".claude" / "agents",
        "claude-agent",
        desc="Claude agent.",
    )
    plugin = repo / "plugins" / "local-plugin"
    _skill(plugin / "skills", "plugin-skill")
    source = scan.PluginSource(
        skills_root=plugin / "skills",
        origin="local/local-plugin",
        controlled=True,
    )

    budget = scan.build_context_budget(repo, [source], home=tmp_path / "home")
    paths = {
        entry["path"]
        for entry in budget["metadata_upper_bounds"]["files"]
    }

    assert ".claude/skills/claude-skill/SKILL.md" in paths
    assert ".agents/skills/agents-skill/SKILL.md" in paths
    assert ".claude/agents/claude-agent.agent.md" in paths
    assert (
        "<plugin:local/local-plugin>/skills/plugin-skill/SKILL.md"
        in paths
    )


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
    local_settings = repo / ".github" / "copilot" / "settings.local.json"
    local_settings.write_text(json.dumps({
        "hooks": {
            "sessionStart": [
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
    assert context_hooks["count"] == 4
    assert context_hooks["emitted_payload_size"] == "unknown"
    assert {
        (entry["path"], entry["event"]) for entry in context_hooks["registrations"]
    } == {
        ("<plugin:market/plugin>/hooks.json", "sessionStart"),
        ("<personal-copilot>/hooks/personal.json", "notification"),
        (".github/copilot/settings.json", "postToolUseFailure"),
        (".github/copilot/settings.local.json", "sessionStart"),
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
    output = capsys.readouterr().out
    payload = json.loads(output)
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
        "repository_conditional_instruction_files",
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


def test_json_from_settings_includes_identity_role_inventory(
    tmp_path: Path, capsys, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    installed = home / ".copilot" / "installed-plugins"
    _aggregate_authority(installed)
    _settings(
        repo,
        {"context-injection@copilot-extensions": True},
        {},
        aggregation=_adoption(),
    )
    copilot = home / ".copilot"
    copilot.mkdir(parents=True, exist_ok=True)
    (copilot / "config.json").write_text(
        json.dumps({"trustedFolders": [str(repo)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan.Path, "home", lambda: home)

    assert scan.main([str(repo), "--json", "--from-settings"]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["session_context"] == {
        "disposition": "repository-authority-proven",
        "authority_proven": True,
        "plugins": [{
            "identity": "copilot-extensions/context-injection",
            "role": "aggregate-authority",
            "session_start": "yes",
            "declaration": "complete",
            "possible_non_empty": "yes",
        }],
    }
    assert not any(
        finding["check"] == "session-context-authority"
        and finding["severity"] == scan.BLOCKING
        for finding in payload["findings"]
    )


def test_from_settings_ignores_untrusted_repository_settings(
    tmp_path: Path, capsys, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    installed = home / ".copilot" / "installed-plugins"
    _aggregate_authority(installed)
    _settings(
        repo,
        {"context-injection@copilot-extensions": True},
        {},
    )
    monkeypatch.setattr(scan.Path, "home", lambda: home)

    assert scan.main([str(repo), "--json", "--from-settings"]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["session_context"]["plugins"] == []
    assert payload["session_context"]["authority_proven"] is False
    assert "do-not-report-this-command" not in output


def test_from_settings_reports_disabled_installed_mcp_bridge_collision(
    tmp_path: Path, capsys, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    installed = home / ".copilot" / "installed-plugins"
    _installed_plugin(installed, "market-a", "plugin-a")
    _installed_plugin(installed, "market-b", "plugin-b")
    for marketplace, plugin in (("market-a", "plugin-a"), ("market-b", "plugin-b")):
        agents = installed / marketplace / plugin / "agents"
        agents.mkdir(exist_ok=True)
        (agents / "demo.mcp.yaml").write_text(
            "server:\n  url: https://example.com\n",
            encoding="utf-8",
        )
    _settings(
        repo,
        {
            "plugin-a@market-a": True,
            "plugin-b@market-b": False,
        },
        {},
    )
    copilot = home / ".copilot"
    copilot.mkdir(parents=True, exist_ok=True)
    (copilot / "config.json").write_text(
        json.dumps({"trustedFolders": [str(repo)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan.Path, "home", lambda: home)

    assert scan.main([str(repo), "--json", "--from-settings"]) == 0

    payload = json.loads(capsys.readouterr().out)
    findings = [
        finding for finding in payload["findings"]
        if finding["check"] == "mcp-bridge-collision"
    ]
    assert len(findings) == 1
    message = findings[0]["message"]
    assert "plugin-a@market-a (enabled)" in message
    assert "plugin-b@market-b (disabled)" in message
    assert "copilot plugin uninstall plugin-b@market-b" in message
    assert "delete installed-plugin directories manually" in message


def test_custom_instruction_tilde_uses_selected_home(
    tmp_path: Path, monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "selected-home"
    instructions = home / ".instructions"
    instructions.mkdir(parents=True)
    (instructions / "policy.instructions.md").write_text(
        "selected policy\n", encoding="utf-8"
    )
    monkeypatch.setenv(
        "COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "~/.instructions"
    )

    budget = scan.build_context_budget(repo, home=home)

    entries = budget["static_instruction_payloads"][
        "custom_instruction_dir_files"
    ]
    assert len(entries) == 1
    assert entries[0]["path"] == (
        "<custom-instructions-1>/policy.instructions.md"
    )


def test_json_without_context_budget_preserves_default_shape(
    tmp_path: Path, capsys,
):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert scan.main([str(repo), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "context_budget" not in payload
