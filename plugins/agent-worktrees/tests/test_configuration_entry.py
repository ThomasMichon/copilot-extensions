"""The owner-context read must not enter the lifecycle command dispatcher."""

from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from agent_worktrees import config as cfg
from agent_worktrees import configuration_entry as entry
from agent_worktrees import record_config, repos


@pytest.mark.parametrize("argv", [
    [], ["state-root", "--json"], ["state-root", "--configuration"],
    ["state-root", "--configuration", "--configuration"],
    ["state-root", "--configuration", "--json", "--pair"],
    ["state-root", "--configuration", "--json", "--conduct"],
    ["state-root", "--configuration", "--json", "--repo", "other"],
    ["state-root", "--configuration", "--json", "--unknown"],
])
def test_unselected_forms_retain_the_existing_parser(
    monkeypatch: pytest.MonkeyPatch, argv: list[str],
) -> None:
    with monkeypatch.context() as guarded:
        guarded.setattr(Path, "cwd", lambda: pytest.fail("eager context read"))
        guarded.setattr(builtins, "__import__", lambda *a, **k: pytest.fail("eager import"))
        assert entry.select_configuration_read(argv) is None


@pytest.mark.parametrize("flags", [
    ["--configuration", "--json"], ["--json", "--configuration"],
])
@pytest.mark.parametrize("bound", [False, True])
def test_selected_read_uses_native_config_and_preserves_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    flags: list[str], bound: bool,
) -> None:
    from agent_worktrees import config_sources

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / ".git").mkdir()
    local = tmp_path / "state"
    local.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (local / "config.yaml").write_text(yaml.safe_dump({
        "repo_name": "sample", "machine": "local-host",
        "knowledge_repo": "knowledge" if bound else "",
        "repos": {"sample": {"anchor": str(anchor)}},
    }), encoding="utf-8")
    registry = repos.ReposRegistry(repos={
        "sample": repos.RepoEntry(
            name="sample", remote="https://example.test/owner/sample.git",
            paths={platform: str(anchor) for platform in ("windows", "linux", "wsl")},
        ),
        "knowledge": repos.RepoEntry(
            name="knowledge",
            paths={platform: str(knowledge) for platform in ("windows", "linux", "wsl")},
        ),
    })
    monkeypatch.setattr(repos, "read_registry", lambda **kwargs: registry)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: local)
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda *a, **k: {})
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", None)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.chdir(anchor)

    with monkeypatch.context() as guarded:
        guarded.setattr(cfg, "_build_repo_config", lambda *a, **k: pytest.fail("launch policy read"))
        guarded.setattr(cfg, "_parse_pr", lambda *a, **k: pytest.fail("publication policy read"))
        selected = entry.select_configuration_read(["state-root", *flags])
        assert selected is not None
        assert selected() == 0
    result = json.loads(capsys.readouterr().out)
    expected = config_sources.configuration_report(cfg.load_config(
        include_control_plane_related_pr=False, include_knowledge_preferences=False,
    ))
    assert result == expected


@pytest.mark.parametrize("external", [False, True])
def test_identity_read_keeps_native_knowledge_registry_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external: bool,
) -> None:
    from agent_worktrees import config_acquisition, state_root
    from agent_worktrees.config_sources import ConfigSource

    anchor = tmp_path / "anchor"
    knowledge = tmp_path / "knowledge"
    anchor.mkdir()
    knowledge.mkdir()
    (knowledge / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    local = tmp_path / "config.yaml"
    local.write_text(yaml.safe_dump({
        "repo_name": "sample", "machine": "configured", "knowledge_repo": "knowledge",
        "repos": {"sample": {"anchor": str(anchor)}},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda *a, **k: {"stateless": external})
    seen: list[tuple[bool, object]] = []

    def sources(config: cfg.Config, **kwargs: object) -> list[ConfigSource]:
        seen.append((config.default_repo.stateless, kwargs["family"]))
        return [ConfigSource(str(knowledge), "knowledge")] if config.default_repo.stateless else []

    monkeypatch.setattr(state_root, "config_source_anchors", sources)
    loaded: list[str] = []

    def registry(
        callback: object, root: str, config: cfg.Config,
    ) -> dict[str, cfg.MachineEntry]:
        loaded.append(root)
        return {"canonical": cfg.MachineEntry(
            key="canonical", hostname="native-host", alias="configured",
            display_name="Machine", environment="Windows 11",
        )}

    monkeypatch.setattr(config_acquisition, "_execution_registry", registry)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "native-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "detect_machine", lambda **k: k.get("value", "native-host"))
    result = cfg.load_configuration_identity(local)
    assert seen == [(external, "registry")]
    assert result.machine == "configured"
    assert loaded == ([str(knowledge)] if external else [])


def test_nested_worktree_project_discovery_needs_no_command_dispatcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import projects_registry

    anchor = tmp_path / "anchor"
    common = anchor / ".git"
    admin = common / "worktrees" / "tree"
    admin.mkdir(parents=True)
    checkout = tmp_path / "tree"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    child = checkout / "nested"
    child.mkdir()
    seen: list[Path] = []

    def registered_project(path: Path) -> str | None:
        seen.append(path)
        return "sample" if path == anchor else None

    monkeypatch.setattr(
        projects_registry, "registered_project_for_anchor",
        registered_project,
    )
    original = builtins.__import__

    def forbid_dispatcher(
        name: str, globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None, fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        assert name not in {"agent_worktrees.front_door_cli", "agent_worktrees.__main__"}
        assert not {"front_door_cli", "__main__"}.intersection(fromlist or ())
        return original(name, globals, locals, fromlist, level)

    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "__import__", forbid_dispatcher)
        assert record_config.metadata_project(None, cwd=child) == "sample"
    assert seen == [common, anchor]


def test_resident_discovery_refuses_directory_relative_registry_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import git_ops, projects_registry

    anchor = tmp_path / "caller"
    anchor.mkdir()
    projects = {"projects": {
        "relative": {"anchor": "caller"}, "sample": {"anchor": str(anchor)},
    }}

    def resolve(**options: bool) -> str | None:
        return projects_registry.project_for_anchor(
            anchor, read_projects=lambda: projects,
            read_repos=lambda: repos.ReposRegistry(), platform=cfg.detect_platform,
            normalize=git_ops._normalize_wt_path, **options,
        )

    monkeypatch.chdir(anchor)
    assert resolve() == "sample"
    monkeypatch.chdir(tmp_path)
    assert resolve() == "relative"
    with pytest.raises(ValueError, match="absolute registered anchor"):
        resolve(absolute_only=True)
