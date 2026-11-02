from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import asyncio

import pytest
import yaml
from types import SimpleNamespace

from worktree_manager import harness_state
from worktree_manager.production_picker import context, project_config


def test_uncached_scalar_never_starts_or_waits_on_an_event_loop(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        project_config.engine_client, "get_value",
        lambda *args: calls.append(args) or str(tmp_path),
    )
    project_config.bind_bootstrap("example", None)

    async def exercise():
        with pytest.raises(RuntimeError, match="configuration is not ready"):
            project_config._project_dir("example")
        assert calls == []
        assert await asyncio.to_thread(project_config._project_dir, "example") == tmp_path
        assert project_config._project_dir("example") == tmp_path

    try:
        asyncio.run(exercise())
        assert calls == [("example", "config-dir")]
    finally:
        project_config.clear_caches(reset_binding=True)


def test_rebinding_does_not_wait_for_or_accept_old_scalar_reader(monkeypatch, tmp_path):
    entered, release, rebound = threading.Event(), threading.Event(), threading.Event()
    project_config.bind_bootstrap("example", None)

    def old_read(*args):
        entered.set()
        assert release.wait(3)
        return str(tmp_path / "old")

    monkeypatch.setattr(project_config.engine_client, "get_value", old_read)
    old = threading.Thread(target=lambda: project_config._project_dir("example"))

    def rebind():
        project_config.bind_bootstrap("example", {"config_dir": str(tmp_path / "new")})
        rebound.set()

    new = threading.Thread(target=rebind)
    try:
        old.start()
        assert entered.wait(1)
        new.start()
        prompt = rebound.wait(0.25)
    finally:
        release.set()
        old.join(2)
        new.join(2)
    try:
        assert prompt, "binding must not wait on an old engine scalar process"
        assert project_config._project_dir("example") == tmp_path / "new"
    finally:
        project_config.clear_caches(reset_binding=True)


def test_failed_scalar_retries_after_file_cache_invalidation(monkeypatch, tmp_path):
    calls = []

    def read(project, key):
        calls.append((project, key))
        if len(calls) == 1:
            raise project_config.engine_client.EngineError("temporary timeout")
        return str(tmp_path)

    monkeypatch.setattr(project_config.engine_client, "get_value", read)
    project_config.bind_bootstrap("example", {"machine": "host"})
    try:
        assert project_config._project_dir("example") != tmp_path
        project_config.clear_caches()
        assert project_config._project_dir("example") == tmp_path
        assert project_config._machine_name("example") == "host"
        assert calls == [("example", "config-dir"), ("example", "config-dir")]
    finally:
        project_config.clear_caches(reset_binding=True)


def test_old_project_file_read_cannot_repopulate_new_binding(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def read(path):
        if path.parent.name == "old":
            entered.set()
            assert release.wait(3)
        return {"generation": path.parent.name}

    monkeypatch.setattr(project_config, "_read_yaml", read)
    project_config.bind_bootstrap("example", {"config_dir": str(tmp_path / "old")})
    errors = []

    def old_read():
        try:
            project_config._load_project_yaml("example")
        except RuntimeError as exc:
            errors.append(str(exc))

    old = threading.Thread(target=old_read)
    try:
        old.start()
        assert entered.wait(1)
        project_config.bind_bootstrap("example", {"config_dir": str(tmp_path / "new")})
        release.set()
        old.join(2)
        assert len(errors) == 1 and "superseded" in errors[0]
        assert project_config._load_project_yaml("example") == {"generation": "new"}
    finally:
        release.set()
        old.join(2)
        project_config.clear_caches(reset_binding=True)


def test_worker_recovery_replaces_fallback_after_input_read(monkeypatch, tmp_path):
    recovered = tmp_path / "recovered"
    recovered.mkdir()
    (recovered / "config.yaml").write_text("repo_name: recovered\n", encoding="utf-8")
    calls, recovering = [], False

    def read(project, key):
        calls.append(key)
        if not recovering:
            raise project_config.engine_client.EngineError("temporary timeout")
        return "recovered-host" if key == "machine" else str(recovered)

    monkeypatch.setattr(project_config.engine_client, "get_value", read)
    monkeypatch.setattr(harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(harness_state, "project_config", lambda name: {"machine": "fallback-host"})
    monkeypatch.setattr(
        harness_state, "build_projects",
        lambda: [SimpleNamespace(name="example", anchor=str(tmp_path / "fallback"))],
    )
    monkeypatch.setattr(harness_state, "repos_registry", lambda: {})
    context.set_project("example")
    project_config.bind_bootstrap("example", None)
    try:
        assert project_config.load_config().machine == "fallback-host"
        assert len(calls) == 3
        project_config.clear_caches()
        recovering = True

        async def exercise():
            with pytest.raises(RuntimeError, match="file configuration is not ready"):
                project_config.load_config()
            assert project_config._machine_name("example") == "fallback-host"
            assert project_config._repo_dir("example") == str(tmp_path / "fallback")
            assert project_config._project_dir("example") == tmp_path / ".example"
            assert len(calls) == 3
            result = await asyncio.to_thread(project_config.load_config)
            assert result.repo_name == "recovered" and result.machine == "recovered-host"
            assert project_config.load_config() == result
            assert len(calls) == 6

        asyncio.run(exercise())
    finally:
        project_config.clear_caches(reset_binding=True)
        context.reset()


def test_optional_bootstrap_scalars_avoid_legacy_calls(monkeypatch, tmp_path):
    def unexpected(*args):
        raise AssertionError(args)

    monkeypatch.setattr(project_config.engine_client, "get_value", unexpected)
    project_config.bind_bootstrap("demo", {
        "config_dir": str(tmp_path / "config"),
        "repo_dir": str(tmp_path / "repo"),
        "machine": "host",
    })
    try:
        assert project_config._project_dir("demo") == tmp_path / "config"
        assert project_config._repo_dir("demo") == str(tmp_path / "repo")
        assert project_config._machine_name("demo") == "host"
    finally:
        project_config.clear_caches(reset_binding=True)


def test_config_dir_only_preserves_effective_repo_and_machine_queries(monkeypatch, tmp_path):
    calls = []
    configured_anchor = str(tmp_path / "configured-anchor")

    def read(project, key):
        calls.append((project, key))
        return {"repo-dir": configured_anchor, "machine": "host"}[key]

    monkeypatch.setattr(project_config.engine_client, "get_value", read)
    project_config.bind_bootstrap("example", {"config_dir": str(tmp_path / "state")})
    try:
        for _ in range(2):
            assert project_config._project_dir("example") == tmp_path / "state"
            assert project_config._repo_dir("example") == configured_anchor
            assert project_config._machine_name("example") == "host"
            project_config.clear_caches()
        assert calls == [("example", "repo-dir"), ("example", "machine")]
    finally:
        project_config.clear_caches(reset_binding=True)


def test_legacy_scalars_are_single_flight_and_project_scoped(monkeypatch, tmp_path):
    calls = []

    def get_value(project, key):
        calls.append((project, key))
        return str(tmp_path / project) if key != "machine" else "host"

    monkeypatch.setattr(project_config.engine_client, "get_value", get_value)
    project_config.bind_bootstrap("demo", None)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert len(list(pool.map(project_config._project_dir, ["demo"] * 12))) == 12
        for _ in range(2):
            project_config._repo_dir("demo")
            project_config._machine_name("demo")
        assert calls == [("demo", "config-dir"), ("demo", "repo-dir"), ("demo", "machine")]
        project_config.bind_bootstrap("other", {"config_dir": "relative", "machine": 23})
        assert project_config._project_dir("other") == tmp_path / "other"
        assert calls[-1] == ("other", "config-dir")
    finally:
        project_config.clear_caches(reset_binding=True)


def _project(name: str, anchor: Path) -> harness_state.ProjectInfo:
    return harness_state.ProjectInfo(
        name=name,
        config_dir=None,
        expose_agent=False,
        knowledge_repo=None,
        profiles=0,
        repo=None,
        anchor=str(anchor),
    )


def test_load_config_uses_registry_default_branch_when_inrepo_config_missing(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "demo"
    project_root.mkdir()

    monkeypatch.setattr(
        harness_state,
        "build_projects",
        lambda home_dir=None: [_project("demo", project_root)],
    )
    monkeypatch.setattr(harness_state, "home", lambda: tmp_path)
    registry = tmp_path / ".agent-worktrees" / "repos.yaml"
    registry.parent.mkdir()
    registry.write_text("repos:\n  demo:\n    default_branch: dev\n")
    monkeypatch.setattr(
        harness_state,
        "project_config",
        lambda name, home_dir=None: {"machine": "host"} if name == "demo" else {},
    )
    context.set_project("demo")
    try:
        cfg = project_config.load_config()
    finally:
        context.reset()

    assert cfg.repo_name == "demo"
    assert cfg.default_repo.anchor == str(project_root)
    assert cfg.default_repo.default_branch == "dev"


@pytest.mark.parametrize("adopted", [True, False])
def test_machines_yaml_path_uses_knowledge_repo_overlay(monkeypatch, tmp_path, adopted):
    launch_root = tmp_path / "launch"
    launch_root.mkdir()
    knowledge_root = tmp_path / "knowledge"
    overlay = knowledge_root / ".agent-worktrees" / "machines.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("machines: {}\n", encoding="utf-8")

    monkeypatch.setattr(harness_state, "home", lambda: tmp_path)
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "projects": {"knowledge": {"anchor": str(knowledge_root)}} if adopted else {},
    }))
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "repos": {"knowledge": {
            "class": "knowledge", harness_state._platform_key(): str(knowledge_root),
        }},
    }))
    config_dir = tmp_path / "accepted-config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("knowledge_repo: knowledge\n")
    project_config.bind_bootstrap("demo", {"config_dir": str(config_dir)})
    monkeypatch.setattr(harness_state, "build_projects", lambda *a: pytest.fail("all-project scan"))
    context.set_project("demo")
    try:
        resolved = project_config.machines_yaml_path(launch_root)
    finally:
        context.reset()
        project_config.clear_caches(reset_binding=True)

    assert resolved == overlay


def test_machines_yaml_path_prefers_active_repo_registry(monkeypatch, tmp_path):
    launch_root = tmp_path / "launch"
    local = launch_root / ".agent-worktrees" / "machines.yaml"
    local.parent.mkdir(parents=True)
    local.write_text("machines: {}\n", encoding="utf-8")
    knowledge_root = tmp_path / "knowledge"
    overlay = knowledge_root / ".agent-worktrees" / "machines.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("machines: {shadow: {}}\n", encoding="utf-8")

    monkeypatch.setattr(
        harness_state,
        "build_projects",
        lambda home_dir=None: [
            _project("demo", launch_root),
            _project("knowledge", knowledge_root),
        ],
    )
    monkeypatch.setattr(
        harness_state,
        "project_config",
        lambda name, home_dir=None: {"knowledge_repo": "knowledge"} if name == "demo" else {},
    )
    context.set_project("demo")
    try:
        resolved = project_config.machines_yaml_path(launch_root)
    finally:
        context.reset()

    assert resolved == local


def test_load_machines_yaml_merges_canonical_and_legacy_disjoint_keys(tmp_path):
    """Regression (ThomasMichon/copilot-extensions#7914): the canonical
    in-repo ``.agent-worktrees/machines.yaml`` (container-fleet entries) must
    never make every real facility machine in the legacy root file disappear
    from the Picker's multi-machine roster -- the canonical file's own
    header documents this as strictly additive. Before the fix,
    ``_load_machines_yaml`` read only whichever file ``machines_yaml_path``
    resolved first, so a Windows Worktree Manager Picker showed only its own
    local tab, no remote machines, the instant the in-repo file existed."""
    root = tmp_path / "repo"
    canonical = root / ".agent-worktrees" / "machines.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "machines:\n  container-fleet:\n    display_name: Container Fleet\n",
        encoding="utf-8",
    )
    legacy = root / "machines.yaml"
    legacy.write_text(
        "machines:\n  machine-a:\n    display_name: Machine A\n"
        "  machine-b:\n    display_name: Machine B\n",
        encoding="utf-8",
    )
    entries = project_config.load_machines_yaml(root)
    assert set(entries) == {"container-fleet", "machine-a", "machine-b"}


def test_load_machines_yaml_canonical_wins_on_key_collision(tmp_path):
    root = tmp_path / "repo"
    canonical = root / ".agent-worktrees" / "machines.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "machines:\n  m1:\n    display_name: Canonical\n", encoding="utf-8"
    )
    legacy = root / "machines.yaml"
    legacy.write_text(
        "machines:\n  m1:\n    display_name: Legacy\n", encoding="utf-8"
    )
    entries = project_config.load_machines_yaml(root)
    assert entries["m1"].display_name == "Canonical"
