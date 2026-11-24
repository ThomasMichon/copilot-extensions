"""Functional, stage-only matrix for two harnesses sharing one footprint."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from test_application_footprints import application_set
from worktree_manager import application_footprints, harness_state, registered_suite as suite, source_config

OLD = "1" * 40
NEW = "2" * 40
BASE = {"worktree-manager", "shared", "provider", "app-a", "app-b"}


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prepared(directory: Path, version: str, commit: str, names: set[str]) -> suite.PreparedSuite:
    directory.mkdir()
    footprint = application_set(directory, [
        (name, version, ["provider"] if name == "shared" else []) for name in sorted(names)
    ])
    catalog = []
    for name in sorted(names - {"worktree-manager", "provider"}):
        catalog.append({"name": name, "source": f"./plugins/{name}"})
        write(directory / "plugins" / name / "plugin.json", {"name": name, "runtimeScope": "universal"})
    write(directory / ".github/plugin/marketplace.json", {"name": "copilot-extensions", "plugins": catalog})
    return suite.PreparedSuite(commit, directory, footprint)


def fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_standalone_fleet_is_selected_by_service_binding_target(tmp_path: Path) -> None:
    root, anchor, knowledge = tmp_path / "install", tmp_path / "harness", tmp_path / "knowledge"
    write(root / "registry/repos.yaml", {"repos": {"h": {harness_state._platform_key(): str(anchor)}}})
    write(root / "registry/projects.yaml", {"projects": {"h": {}}})
    write(root / "registry/footprint.json", {"schema": "copilot-extensions.suite-footprint", "version": 1,
                                            "root": str(root), "default_project": "h"})
    write(anchor / ".github/copilot/settings.json", {"enabledPlugins": {}})
    catalog = prepared(tmp_path / "suite", "1.0", OLD, {"worktree-manager", "fleet-manager"})
    write(catalog.marketplace / ".github/plugin/marketplace.json", {"name": "copilot-extensions", "plugins": []})
    config = knowledge / ".copilot-extensions/fleet-manager/config.json"
    write(config, {"schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "r1",
                   "capacity": {"machine_limit": 2, "execution_hosts": 0},
                   "roles": {"controller": {"kind": "service", "required": True, "available": True}},
                   "extensions": {"role_bindings": {
                       "schema": "fleet-manager.role-bindings", "version": 1,
                       "providers": {"provider": {"implementation": "komodo", "instance_ref": "primary",
                                                 "target": "other", "configuration_ref": "cfg", "bootstrap_ref": "boot"}},
                       "bindings": {"controller": {"implementation": "fleet-manager.gateway", "provider": "provider",
                                                   "target": "selected", "resource": "gateway", "artifact_ref": "app",
                                                   "configuration_ref": "cfg"}}}}})

    def targets(target: str) -> set[str]:
        return {app.name for app in suite.registered_plan(
            root, resolve=lambda *_: catalog,
            observe=lambda *_: suite.HarnessPolicy((), (target,), fleet_config=config, fleet_target=target),
        )}

    policy = json.loads(config.read_bytes())
    inventory = policy["extensions"]["role_bindings"]
    assignments = inventory["bindings"]
    inventory["bindings"] = {}
    write(config, policy)
    assert targets("selected") == {"worktree-manager"}
    provider = inventory["providers"]["provider"]
    implementation = provider.pop("implementation")
    write(config, policy)
    with pytest.raises(ValueError, match="Fleet"):
        targets("selected")
    provider["implementation"] = implementation
    inventory["bindings"] = assignments
    write(config, policy)
    assert targets("selected") == {"worktree-manager", "fleet-manager"}
    assert targets("other") == {"worktree-manager"}
    policy["roles"]["custom"] = {"description": "application-specific role"}
    bindings = policy["extensions"]["role_bindings"]["bindings"]
    bindings["custom"] = {**bindings["controller"], "target": "custom-host", "resource": "custom-app"}
    write(config, policy)
    assert targets("selected") == {"worktree-manager", "fleet-manager"}
    with pytest.raises(ValueError, match="Fleet"):
        targets("custom-host")
    config.unlink()
    with pytest.raises(ValueError, match="Fleet"):
        targets("selected")
    assert not (root / "apps").exists()


@pytest.mark.timeout(5)
def test_fleet_policy_refuses_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "policy"
    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is None:
        pytest.skip("POSIX FIFO")
    mkfifo(fifo)
    with pytest.raises(ValueError, match="Fleet"):
        suite.fleet_service_roles(fifo, "target")


@pytest.mark.parametrize("case", ["fresh", "same-source-update", "missing-app", "same-pin", "switch-default", "bare-default"])
def test_registered_stage_matrix(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "install"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    events = tmp_path / "setup-events"
    monkeypatch.setenv("FOOTPRINT_TEST_LOG", str(events))
    x = prepared(tmp_path / "old", "1.0", OLD, BASE)
    y = prepared(tmp_path / "new", "2.0", NEW, BASE | {"new-app"})
    calls: list[str] = []

    def resolve(repository: str, ref: str) -> suite.PreparedSuite:
        assert repository == source_config.DEFAULT_REPO
        calls.append(ref)
        return {OLD: x, "main": y}[ref]

    def register(names: tuple[str, ...], default: str = "A") -> None:
        reg = root / "registry"
        write(reg / "repos.yaml", {"repos": {n: {harness_state._platform_key(): str(tmp_path / n)} for n in names}})
        write(reg / "projects.yaml", {"projects": {n: {} for n in names}})
        write(reg / "footprint.json", {"schema": "copilot-extensions.suite-footprint", "version": 1,
                                       "root": str(root), "default_project": default})

    def demand(name: str, ref: str, *apps: str) -> None:
        write(tmp_path / name / ".copilot-extensions/config.yaml", {"current_version": ref})
        write(tmp_path / name / ".github/copilot/settings.json",
              {"enabledPlugins": {n + "@copilot-extensions": True for n in apps}})

    def stage(expected: set[str], version: str) -> None:
        calls.clear()
        planned = suite.registered_plan(root, resolve=resolve, observe=lambda *_: suite.HarnessPolicy((), ()))
        assert calls == ([OLD] if version == "1.0" else ["main"])
        targets = [a.name for a in planned]
        assert set(targets) == expected and len(targets) == len(expected)
        assert targets.index("provider") < targets.index("shared")
        previous = events.read_text().splitlines() if events.exists() else []
        application_footprints.install(planned, stage=True, deadline=time.monotonic() + 120)
        assert events.read_text().splitlines() == previous + [f"{n}:stage" for n in targets]
        assert all(a.version == version and a.slot.is_dir() for a in planned)
        assert not any((a.root / marker).exists() for a in planned
                       for marker in ("current-version", "last-known-good", "bin"))
        output = capsys.readouterr()
        assert not output.err and not output.out

    def refused() -> None:
        before = fingerprint(root / "apps")
        prior_events = events.read_bytes() if events.exists() else None
        calls.clear()
        with pytest.raises(ValueError) as caught:
            stage(BASE, "1.0")
        assert str(caught.value) == (
            f"Harness B, selected commit {OLD}: Enabled plugin absent or ambiguous in selected source: new-app"
        )
        assert calls == [OLD] and before == fingerprint(root / "apps")
        assert prior_events == (events.read_bytes() if events.exists() else None)

    demand("A", OLD, "shared", "app-a")
    demand("B", OLD if case == "same-pin" else "main", "shared", "app-b")
    register(("A", "B"))
    if case == "bare-default":
        anchor = tmp_path / "A"
        (anchor / "objects").mkdir()
        (anchor / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
        with pytest.raises(ValueError, match="materialized worktree"):
            suite.registered_plan(root, resolve=resolve, observe=lambda *_: pytest.fail("unexpected policy read"))
        assert not calls and not (root / "apps").exists()
    elif case == "fresh":
        stage(BASE, "1.0")
    elif case == "same-source-update":
        register(("A",))
        stage(BASE - {"app-b"}, "1.0")
        register(("B", "A"))
        stage(BASE, "1.0")
    else:
        b_ref = OLD if case == "same-pin" else "main"
        demand("B", b_ref, "shared", "app-b", "new-app")
        refused()
        demand("B", b_ref, "shared", "app-b")
        stage(BASE, "1.0")
        demand("B", b_ref, "shared", "app-b", "new-app")
        refused()
        if case == "switch-default":
            before = fingerprint(root / "apps")
            register(("A", "B"), default="B")
            stage(BASE | {"new-app"}, "2.0")
            after = fingerprint(root / "apps")
            assert all(after.get(k) == v for k, v in before.items())
