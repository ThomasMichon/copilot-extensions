"""Application-root updates never publish legacy-format Manager launchers."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import application_footprints as footprints
from worktree_manager import runtime_application_update as update
from worktree_manager import runtime_source_prepare as preparation
from worktree_manager import runtime_source as acquisition
from worktree_manager import self_install, source_config
from worktree_manager.runtime_source import ResolvedSource, SourceAcquisitionError
from worktree_manager.runtime_source_config import RuntimeSource


def _application_declaration(
    commit: str, tree: str, *, profile: str = "fixture",
) -> dict[str, Any]:
    """Synthetic constructor facts, not wheel bytes or admitted profile evidence."""
    installer = self_install._runtime_installer()
    sources = installer.runtime_source
    identity = sources.SourceIdentity(
        "worktree-manager", "2.0.0", commit, tree, "c" * 64,
        sources.runtime_version("2.0.0", commit, "c" * 64),
    )
    wheel = {
        "version": "2.0.0", "filename": "fixture_manager-2.0.0-py3-none-any.whl",
        "sha256": "sha256:" + "d" * 64,
    }
    binding = {
        "component": "worktree-manager", "approval_source": profile,
        "release": {"identity": "source:" + commit, "sha256": "sha256:" + "c" * 64},
        "wheels": [{"name": "fixture-manager", **wheel}],
    }
    receipt: dict[str, Any] = {
        "construction": "closed-wheels", "binding": binding,
        "lock_id": "sha256:" + hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("ascii"),
        ).hexdigest(),
        "distributions": {"fixture-manager": wheel},
    }
    version = identity.runtime_version + ".profile." + receipt["lock_id"][7:23]
    wheels = installer.engine.WheelReuseInputs(
        version, json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        time.monotonic() + 30,
    )
    return {
        "name": "worktree-manager", "commands": ["worktree-manager"],
        "module": "worktree_manager", "root_environment": "WORKTREE_MANAGER_ROOT",
        "default_directory": ".worktree-manager", "setup": [], "start": [],
        "construction": {
            "identity": sources.manifest_fields(identity), "runtime_version": version,
            "source_hash": "e" * 64,
            "payload_hash": installer.engine._wheel_construction_hash("e" * 64, wheels),
            "wheel_inputs": receipt, "extras": [], "local_packages": ["fixture-manager"],
            "excluded_packages": [],
        },
    }


@pytest.mark.parametrize("phase", [
    "advance", "repeat", "dry-run", "acquire", "provider", "identity", "setup", "selection",
])
def test_application_update_reuses_complete_setup_without_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    root = tmp_path / "custom-install-name"
    previous = "2" if phase == "repeat" else "1"
    selected = root / "versions" / previous
    selected.mkdir(parents=True)
    (selected / "application.json").write_text("{}")
    (root / "current-version").write_text(previous)
    source_config.set_source(repo=str(tmp_path / "upstream"), ref="dev", root=root)
    config = (root / "config.toml").read_bytes()
    monkeypatch.setattr("worktree_manager.runtime_application_update.shutil.which", lambda _: "git")
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *args, **kwargs: {"SOURCE": "fixture"})
    monkeypatch.setattr(self_install, "self_install", lambda **kwargs: pytest.fail("legacy constructor"))
    monkeypatch.setattr(self_install, "_fetch_via_tarball", lambda *args: pytest.fail("legacy fetch"))
    monkeypatch.setattr(update, "preparer", lambda: tmp_path / "trusted-tool")
    calls: list[str] = []
    budget: list[float] = []
    acquisition_roots: list[Path] = []
    commit, tree = "a" * 40, "b" * 40

    @contextmanager
    def acquire(selection: RuntimeSource, **options: Any) -> Iterator[ResolvedSource]:
        assert selection == RuntimeSource("dev", str(tmp_path / "upstream"), allow_dev=True)
        acquisition_roots.append(options["root"])
        assert not options["root"].is_relative_to(root) and options["root"].is_dir()
        assert options["compact"] is True
        assert options["environment"] == {"SOURCE": "fixture"}
        budget.append(options["deadline"])
        calls.append("acquired")
        if phase == "acquire":
            raise SourceAcquisitionError("fixture acquisition failure")
        yield ResolvedSource(selection, tmp_path / "checkout", commit, tree, commit, "c" * 40)
        calls.append("released")

    def prepare(tool: Path, resolved: ResolvedSource, output: Path, **options: Any) -> Path:
        assert tool == tmp_path / "trusted-tool" and resolved.commit == commit
        assert options == {"deadline": budget[0], "environment": {"SOURCE": "fixture"}}
        assert output.parent == acquisition_roots[0] / "footprints"
        assert output.parent.is_dir()
        if phase == "provider":
            raise subprocess.CalledProcessError(1, ["fixture-provider"], stderr="fixture provider unavailable")
        calls.append("prepared")
        return tmp_path / "footprint"

    def plan(footprint: Path, apps_root: Path, **options: Any) -> tuple[footprints.Application, ...]:
        assert calls == ["acquired", "prepared", "released"]
        assert footprint == tmp_path / "footprint" and apps_root == root.parent
        assert options == {"selected": ("worktree-manager",)}
        declaration = json.dumps({"construction": {"identity": {"runtime_source": {
            "source_commit": "d" * 40 if phase == "identity" else commit, "source_tree": tree,
        }}}}).encode()
        return (footprints.Application(
            "worktree-manager", "2", footprint / "apps" / "worktree-manager",
            root.parent / "worktree-manager", "WORKTREE_MANAGER_ROOT", declaration, (),
        ),)

    def install(apps: tuple[footprints.Application, ...], **options: Any) -> None:
        assert len(apps) == 1 and apps[0].root == root
        assert options == {"stage": False, "deadline": budget[0]}
        calls.append("setup")
        if phase == "setup":
            raise subprocess.CalledProcessError(7, ["fixture-setup"])
        if phase != "selection":
            (root / "current-version").write_text("2")

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare_footprint", prepare)
    monkeypatch.setattr(footprints, "plan", plan)
    monkeypatch.setattr(footprints, "select_generation", lambda application, current, **options: application)
    monkeypatch.setattr(footprints, "install", install)
    result = self_install.self_update(root=root, dry_run=phase == "dry-run")
    assert result.previous == previous
    assert (root / "config.toml").read_bytes() == config
    if phase in {"acquire", "provider", "identity", "setup", "selection"}:
        assert result.action == "error"
        if phase == "provider":
            assert result.reason is not None and "fixture provider unavailable" in result.reason
        assert (root / "current-version").read_text() == previous
    else:
        assert result.action == ("already-current" if phase == "repeat" else "updated")
        assert result.version == "2"
        assert (root / "current-version").read_text() == (previous if phase == "dry-run" else "2")
    assert ("setup" in calls) is (phase not in {"acquire", "provider", "identity", "dry-run"})
    assert acquisition_roots and not acquisition_roots[0].exists()


@pytest.mark.parametrize("fail_setup", [False, True])
def test_prepared_update_uses_real_plan_outside_installation_and_holds_inputs_through_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_setup: bool,
) -> None:
    root = tmp_path / "worktree-manager"
    root.mkdir()
    (root / "current-version").write_text("1")
    monkeypatch.setattr("worktree_manager.runtime_application_update.shutil.which", lambda _: "git")
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "worktree_manager.runtime_application_inputs.provider_root",
        lambda: Path(__file__).resolve().parents[2],
    )
    monkeypatch.setattr(update, "preparer", lambda: tmp_path / "trusted-tool")
    monkeypatch.setattr("worktree_manager.runtime_application_update.tempfile.gettempdir", lambda: str(tmp_path))
    source = RuntimeSource("dev", str(tmp_path / "upstream"), allow_dev=True)
    commit, tree = "a" * 40, "b" * 40
    declaration = _application_declaration(commit, tree)
    version = declaration["construction"]["runtime_version"]
    workspace: list[Path] = []
    prepared_inputs: list[Path] = []

    @contextmanager
    def acquire(selection: RuntimeSource, **options: Any) -> Iterator[ResolvedSource]:
        workspace.append(options["root"])
        yield ResolvedSource(source, tmp_path / "checkout", commit, tree, commit, "c" * 40)

    def prepare(tool: Path, resolved: ResolvedSource, output: Path, **options: Any) -> Path:
        footprint = output / "footprint"
        app = footprint / "apps" / "worktree-manager"
        (app / "bin").mkdir(parents=True)
        (footprint / "footprint.json").write_text(json.dumps({
            "schema": "copilot-extensions.application-footprint", "version": 1,
            "apps": [{"name": "worktree-manager", "version": version, "dependencies": []}],
        }))
        (app / "application.json").write_text(json.dumps(declaration))
        for name in ("pyproject.toml", "uv.lock", "bin/setup.ps1", "bin/setup.sh"):
            (app / name).write_text("# fixture\n")
        shutil.copyfile(
            Path(__file__).resolve().parents[2] / "libs" / "installer-engine"
            / "runtime_application_binstubs.py", app / "runtime_application_binstubs.py",
        )
        prepared_inputs.append(app)
        return footprint

    def install(apps: tuple[footprints.Application, ...], **options: Any) -> None:
        assert apps[0].source == prepared_inputs[0] and apps[0].source.is_dir()
        assert workspace[0].is_dir() and not apps[0].source.is_relative_to(root)
        assert apps[0].root == root
        if fail_setup:
            raise subprocess.CalledProcessError(7, ["fixture-setup"])
        (root / "current-version").write_text(version)

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare_footprint", prepare)
    monkeypatch.setattr(footprints, "install", install)
    result = update.update(root, repo=source.repo, ref="dev", dry_run=False)
    assert result.action == ("error" if fail_setup else "updated")
    assert (root / "current-version").read_text() == ("1" if fail_setup else version)
    assert workspace and not workspace[0].exists()
    assert prepared_inputs and not prepared_inputs[0].exists()


@pytest.mark.parametrize("damage", [
    "missing", "truncated", "utf8", "shape", "commands", "oversized",
    "command-name", "environment-name", "runtime-version", "default-directory",
    "unreadable", "helper-syntax", "helper-import", "helper-api", "helper-runtime",
    "frontdoors", "conflict", "incoming", "incoming-module", "incoming-helper",
    "incoming-unreadable", "incoming-command-name", "incoming-environment-name",
    "incoming-runtime-version", "incoming-default-directory", "indirect",
    "construction-field", "source-field", "receipt-field", "package-list", "payload-hash",
    "incoming-construction-field", "incoming-source-field", "incoming-receipt-field",
    "incoming-package-list", "incoming-payload-hash", "source-conflict", "profile-conflict",
    "setup-file", "project-file", "lock-file", "source-file", "payload-file",
    "helper-name", "helper-lookup", "incoming-helper-name", "incoming-helper-lookup",
])
def test_same_source_update_repairs_in_a_distinct_slot_and_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    monkeypatch.setattr(
        "worktree_manager.runtime_application_inputs.provider_root",
        lambda: Path(__file__).resolve().parents[2],
    )
    root = tmp_path / "worktree-manager"
    footprint = tmp_path / "prepared"
    app = footprint / "apps" / "worktree-manager"
    (app / "bin").mkdir(parents=True)
    commit, tree = "a" * 40, "b" * 40
    declaration = _application_declaration(commit, tree)
    version = declaration["construction"]["runtime_version"]
    (footprint / "footprint.json").write_text(json.dumps({
        "schema": "copilot-extensions.application-footprint", "version": 1,
        "apps": [{"name": "worktree-manager", "version": version, "dependencies": []}],
    }), encoding="utf-8")
    (app / "application.json").write_text(json.dumps(declaration), encoding="utf-8")
    for name in ("pyproject.toml", "uv.lock"):
        (app / name).write_text("# fixture\n", encoding="utf-8")
    (app / "runtime-source.json").write_text(json.dumps(
        declaration["construction"]["identity"]["runtime_source"],
    ), encoding="utf-8")
    (app / "resources").mkdir()
    (app / "resources" / "payload.bin").write_bytes(b"fixture payload")
    (app / "bin" / "setup.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$slot = Split-Path -Parent $PSScriptRoot\n"
        "if ($args -notcontains '--stage') {\n"
        "  Set-Content -LiteralPath (Join-Path $env:WORKTREE_MANAGER_ROOT 'current-version') "
        "-Value (Split-Path -Leaf $slot)\n"
        "}\n", encoding="utf-8",
    )
    (app / "bin" / "setup.sh").write_text(
        '#!/bin/sh\nset -eu\nif [ "${1:-}" != "--stage" ]; then\n'
        '  basename "$(dirname "$(dirname "$0")")" > "$WORKTREE_MANAGER_ROOT/current-version"\n'
        "fi\n", encoding="utf-8",
    )
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "libs" / "installer-engine"
        / "runtime_application_binstubs.py", app / "runtime_application_binstubs.py",
    )
    old = root / "versions" / version
    shutil.copytree(app, old)
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    (root / "durable-state").write_bytes(b"preserved")
    metadata = old / "application.json"
    if damage == "missing":
        metadata.unlink()
    elif damage == "truncated":
        metadata.write_bytes(b'{"commands": [')
    elif damage == "utf8":
        metadata.write_bytes(b"\xff")
    elif damage == "shape":
        metadata.write_bytes(b"[]")
    elif damage == "commands":
        metadata.write_text(json.dumps({**declaration, "commands": []}), encoding="utf-8")
    elif damage.removeprefix("incoming-") in {
        "command-name", "environment-name", "runtime-version", "default-directory",
    }:
        corruptions: dict[str, dict[str, Any]] = {
            "command-name": {"commands": ["worktree-manager", "../broken"]},
            "environment-name": {"root_environment": "../broken"},
            "runtime-version": {"construction": {"runtime_version": "../broken"}},
            "default-directory": {"default_directory": "../broken"},
        }
        invalid = corruptions[damage.removeprefix("incoming-")]
        target = app / "application.json" if damage.startswith("incoming-") else metadata
        target.write_text(json.dumps({**declaration, **invalid}), encoding="utf-8")
    elif damage == "oversized":
        metadata.write_bytes(b"x" * 65537)
    elif damage == "conflict":
        metadata.write_text(json.dumps({**declaration, "module": "other_owner"}), encoding="utf-8")
    elif damage in {"setup-file", "project-file", "lock-file", "source-file", "payload-file"}:
        missing = {
            "setup-file": "bin/setup.ps1" if os.name == "nt" else "bin/setup.sh",
            "project-file": "pyproject.toml", "lock-file": "uv.lock",
            "source-file": "runtime-source.json", "payload-file": "resources/payload.bin",
        }[damage]
        (old / missing).unlink()
    elif damage in {"source-conflict", "profile-conflict"}:
        metadata.write_text(json.dumps(_application_declaration(
            "f" * 40 if damage == "source-conflict" else commit, tree,
            profile="other" if damage == "profile-conflict" else "fixture",
        )), encoding="utf-8")
    elif damage.removeprefix("incoming-") in {
        "construction-field", "source-field", "receipt-field", "package-list", "payload-hash",
    }:
        record = json.loads(json.dumps(declaration))
        construction = record["construction"]
        field = damage.removeprefix("incoming-")
        if field == "construction-field":
            del construction["source_hash"]
        elif field == "source-field":
            del construction["identity"]["runtime_source"]["assembly_hash"]
        elif field == "receipt-field":
            del construction["wheel_inputs"]["distributions"]
        elif field == "package-list":
            construction["local_packages"] = [{}]
        else:
            construction["payload_hash"] = "0" * 64
        target = app / "application.json" if damage.startswith("incoming-") else metadata
        target.write_text(json.dumps(record), encoding="utf-8")
    elif damage == "incoming-module":
        (app / "application.json").write_text(json.dumps({
            **declaration, "module": "not a module",
        }), encoding="utf-8")
    elif damage.startswith("helper-") or damage.startswith("incoming-helper"):
        helpers = {
            "helper-syntax": "def unfinished(",
            "helper-import": "import fixture_unavailable_launcher_dependency\n",
            "helper-api": "unavailable_renderer = True\n",
            "helper-runtime": "raise RuntimeError('unavailable selected renderer')\n",
            "helper-name": "unavailable_renderer_name\n",
            "helper-lookup": (
                "def global_binstubs(*args, **kwargs):\n"
                "    raise KeyError('fixture renderer input')\n"
            ),
        }
        key = damage.removeprefix("incoming-")
        key = "helper-syntax" if key == "helper" else key
        damaged = app if damage.startswith("incoming-") else old
        (damaged / "runtime_application_binstubs.py").write_text(
            helpers[key], encoding="utf-8",
        )
    elif damage in {"unreadable", "incoming-unreadable"}:
        read = footprints._read
        unreadable = (app if damage == "incoming-unreadable" else old) / "application.json"

        def guarded_read(path: Path) -> bytes:
            if path == unreadable:
                raise PermissionError("fixture declaration read denied")
            return read(path)

        monkeypatch.setattr(footprints, "_read", guarded_read)
    elif damage == "indirect":
        original = Path.lstat

        def indirect(path: Path, *args: Any, **kwargs: Any) -> Any:
            info = original(path, *args, **kwargs)
            if path == metadata:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400,
                )
            return info

        monkeypatch.setattr(Path, "lstat", indirect)
    elif damage != "frontdoors":
        (app / "application.json").write_bytes(b'{"commands": [')
    before = {path.relative_to(old): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in old.rglob("*") if path.is_file()}
    source = RuntimeSource("dev", str(tmp_path / "upstream"), allow_dev=True)
    source_config.set_source(repo=source.repo, ref="dev", root=root)

    @contextmanager
    def acquire(*args: Any, **options: Any) -> Iterator[ResolvedSource]:
        yield ResolvedSource(source, tmp_path / "checkout", commit, tree, commit, "c" * 40)

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "preparer", lambda: tmp_path / "trusted-tool")
    monkeypatch.setattr(update, "prepare_footprint", lambda *args, **options: footprint)
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *args, **options: {})
    monkeypatch.setattr(self_install, "self_install", lambda **options: pytest.fail("legacy constructor"))
    result = self_install.self_update(root=root)
    if damage.startswith("incoming") or damage in {"conflict", "source-conflict", "profile-conflict", "indirect"}:
        assert result.action == "error"
        assert (root / "current-version").read_text(encoding="utf-8").strip() == version
        assert list((root / "versions").iterdir()) == [old]
    elif damage == "frontdoors":
        assert result.action == "already-current", result.reason
        assert result.version == version
        assert {path for path in (root / "versions").iterdir() if not path.name.startswith(".")} == {old}
    else:
        assert result.action == "updated", result.reason
        assert result.version is not None and result.version.startswith(version + ".repair.")
        repaired = root / "versions" / result.version
        assert (repaired / "application.json").read_bytes() == (app / "application.json").read_bytes()
        repeated = self_install.self_update(root=root)
        assert repeated.action == "already-current", repeated.reason
        assert repeated.version == result.version
        assert (root / "current-version").read_text(encoding="utf-8").strip() == result.version
        assert {path for path in (root / "versions").iterdir() if not path.name.startswith(".")} == {
            old, repaired,
        }
    assert (root / "durable-state").read_bytes() == b"preserved"
    assert {path.relative_to(old): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in old.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("case", ["missing-git", "auth"])
def test_application_update_refuses_before_source_or_legacy_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    from worktree_manager.update_auth import AuthRequired

    monkeypatch.setattr(
        "worktree_manager.runtime_application_update.shutil.which",
        lambda _: None if case == "missing-git" else "git",
    )
    monkeypatch.setattr(update, "acquire", lambda *args, **kwargs: pytest.fail("no acquisition"))

    def auth(*args: Any, **kwargs: Any) -> None:
        if case == "missing-git":
            pytest.fail("no auth")
        raise AuthRequired("fixture authentication required")

    monkeypatch.setattr("worktree_manager.update_auth.environment", auth)
    result = update.update(tmp_path / "manager", repo=str(tmp_path / "source"), ref="main", dry_run=False)
    assert result.action == ("skipped" if case == "missing-git" else "auth_required")


@pytest.mark.parametrize("receipt", ["valid", "other", "late"])
@pytest.mark.parametrize("local_source", [False, True])
def test_footprint_preparation_uses_packaged_producer_and_one_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt: str, local_source: bool,
) -> None:
    provider = tmp_path / "trusted" / "application_footprint.py"
    provider.parent.mkdir()
    provider.write_text("# fixture")
    output = tmp_path / "prepared"
    selection = RuntimeSource("main", str(tmp_path / "source")) if local_source else RuntimeSource("main")
    resolved = ResolvedSource(selection, tmp_path / "checkout", "a" * 40, "b" * 40, "a" * 40, "a" * 40)
    deadline = time.monotonic() + 30

    def run(command: list[str], **options: Any) -> SimpleNamespace:
        assert str(provider) in command and command[command.index("--source-commit") + 1] == resolved.commit
        assert command[command.index("--branch") + 1] == "main"
        assert options["cwd"] == (Path(provider.anchor) if os.name == "nt" else provider.parent)
        assert options["deadline"] == deadline
        assert 0 < float(command[command.index("--timeout") + 1]) <= 30
        if local_source:
            assert json.loads(command[command.index("--source-descriptor") + 1]) == {
                "source": "directory", "path": selection.repo,
            }
        else:
            assert "--source-descriptor" not in command
        if receipt == "late":
            monkeypatch.setattr("worktree_manager.runtime_source_prepare.time.monotonic", lambda: deadline + 1)
        return SimpleNamespace(stdout=json.dumps({
            "footprint": str(tmp_path / "other" if receipt == "other" else output / "footprint"),
        }))

    monkeypatch.setattr(preparation, "_runtime_installer", lambda: SimpleNamespace(engine=SimpleNamespace(
        run_owned=run, _native_retained_path=lambda path: str(path),
    )))
    if receipt == "valid":
        assert preparation.prepare_footprint(
            provider.parent / "prepare_runtime_source.py", resolved, output, deadline=deadline,
        ) == output / "footprint"
    else:
        with pytest.raises(ValueError, match="different output|over-budget"):
            preparation.prepare_footprint(
                provider.parent / "prepare_runtime_source.py", resolved, output, deadline=deadline,
            )


def test_preparer_prefers_complete_packaged_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for relative in ("runtime-source-tools/tools", "runtime-input-provider/tools", "runtime-source-tools"):
        directory = tmp_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "prepare_runtime_source.py").write_text("# fixture")
    monkeypatch.setattr(preparation, "_runtime_scripts", lambda: tmp_path)
    assert preparation.preparer() == tmp_path / "runtime-source-tools" / "tools" / "prepare_runtime_source.py"


def test_packaged_footprint_producer_composes_without_checkout_tool_imports(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    project = repository / "worktree-manager"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.0.0"\n',
    )
    checkout = Path(__file__).resolve().parents[2]
    templates = repository / "libs" / "payload-invocation" / "templates"
    shutil.copytree(checkout / "libs" / "payload-invocation" / "templates", templates)
    (templates.parent / "generate.py").write_text("raise AssertionError('fetched tooling executed')\n")
    plugin = repository / "plugins" / "sample"
    (plugin / "scripts").mkdir(parents=True)
    for name in ("install.sh", "install.ps1", "resolve-runtime.sh", "resolve-runtime.ps1"):
        (plugin / "scripts" / name).write_text("# fixture installer input\n")
    (plugin / "payload-invocation.json").write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "version": 1,
        "command": "sample", "module": "sample.cli", "purpose": "Sample command",
        "runtimeRoot": ".sample", "noSelfProvisionEnv": "SAMPLE_NO_PROVISION",
    }))
    bundle = tmp_path / "packaged"
    declaration = json.loads((checkout / "worktree-manager" / "packaged-assets.json").read_text())
    for group in declaration["groups"]:
        if group["canonicalDir"] == "libs/installer-engine":
            for name in group["files"]:
                destination = repository / group["canonicalDir"] / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(checkout / group["canonicalDir"] / name, destination)
        prefix = "scripts/runtime-source-tools/"
        if not group["payloadDir"].startswith(prefix):
            continue
        for name in group["files"]:
            destination = bundle / group["payloadDir"].removeprefix(prefix) / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checkout / group["canonicalDir"] / name, destination)
    generator = bundle / "libs" / "payload-invocation" / "generate.py"
    cached_generator = tmp_path / "cached-generator.py"
    generator.rename(cached_generator)
    os.link(cached_generator, generator)
    acquisition._git(repository, "init", "-q", "-b", "main")
    acquisition._git(repository, "add", ".")
    acquisition._git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "Fixture application source",
    )
    commit = acquisition._git(repository, "rev-parse", "HEAD")
    producer = bundle / "tools" / "application_footprint.py"
    code = (
        "import json,sys,time; from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "import application_footprint,local_install_source; "
        "assert Path(application_footprint.__file__)==Path(sys.argv[1]); "
        "repo=Path(sys.argv[2]); output=Path(sys.argv[4]); "
        "record=local_install_source.compose(repo,sys.argv[3],sys.argv[3],output,"
        "source_descriptor={'source':'directory','path':str(repo)},"
        "deadline=time.monotonic()+60); "
        "assert record['composition']['main_seed']==sys.argv[3]; "
        "assert (output/'source/worktree-manager/pyproject.toml').is_file(); "
        "assert 'sample.cli' in (output/'source/plugins/sample/bin/sample').read_text(); "
        "import runtime_application_install as owner,installer_engine as engine,versioned_runtime as runtime; "
        "assert Path(owner.__file__).is_relative_to(Path(sys.argv[1]).parent.parent); "
        "application=json.loads(sys.argv[5]); "
        "facts=owner._construction_metadata(application,runtime,engine,time.monotonic()+30); "
        "slot=output/'versions'/(facts.inputs['runtime_version']+'.repair.'+'0'*32); "
        "slot.mkdir(parents=True); "
        "(slot/'application.json').write_text(json.dumps(application)); "
        "(slot/'runtime-source.json').write_text(json.dumps(facts.source)); "
        "associated=owner._construction_inputs(slot,application,runtime,engine,time.monotonic()+30); "
        "assert associated.receipt==facts.receipt and associated.wheels.generation==slot.name; "
        "print(json.dumps({'composed':True}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(producer),
         str(repository), commit, str(tmp_path / "composed"),
         json.dumps(_application_declaration("a" * 40, "b" * 40))],
        capture_output=True, text=True, timeout=90, cwd=tmp_path,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"composed": True}
    profile = subprocess.run(
        [sys.executable, "-I", str(bundle / "tools" / "local_install_profile.py"), "--help"],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
        env={**os.environ, "PYTHONSAFEPATH": "1"}, **no_window_kwargs(),
    )
    assert profile.returncode == 0, profile.stdout + profile.stderr
    assert "--application-components" in profile.stdout
    closed = subprocess.run(
        [
            sys.executable, "-I", "-B", "-c",
            "import sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); import local_install_closed_wheels as owner; "
            "assert Path(owner.__file__).parent == Path(sys.argv[1]); "
            "assert owner.CONTRACT == {"
            "'schema':'copilot-extensions.closed-wheel-source-preparation','version':1}; "
            "assert 'closed-wheel-source/v1' in "
            "owner.profiles.source_inputs.APPLICATION_INSTALL_CAPABILITIES",
            str(bundle / "tools"),
        ],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
        **no_window_kwargs(),
    )
    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert not list(bundle.rglob("*.pyc"))


@pytest.mark.skipif(os.name != "nt", reason="Windows Git ordinary-path length boundary")
def test_native_acquisition_under_a_deep_application_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    acquisition._git(repository, "init", "-q", "-b", "main")
    (repository / "payload.txt").write_text("fixture source\n")
    acquisition._git(repository, "add", ".")
    acquisition._git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "Fixture source",
    )
    root = tmp_path / ("deep-app-" + "x" * max(1, 208 - len(str(tmp_path)) - 10))
    temporary = tmp_path / ("temp-" + "x" * max(1, 156 - len(str(tmp_path)) - 6))
    temporary.mkdir()
    monkeypatch.setattr("worktree_manager.runtime_application_update.tempfile.gettempdir", lambda: str(temporary))
    with update._acquisition_root(root) as cache:
        with acquisition.acquire(
            RuntimeSource("main", str(repository)), root=cache, compact=True,
        ) as resolved:
            assert (resolved.checkout / "payload.txt").read_text() == "fixture source\n"
            worktree = resolved.checkout / ".promote-scratch" / "promote-fixture"
            worktree.parent.mkdir()
            acquisition._git(resolved.checkout, "worktree", "add", "--detach", str(worktree), resolved.commit)
            try:
                assert acquisition._git(worktree, "rev-parse", "HEAD") == resolved.commit
                assert (worktree / "payload.txt").read_text() == "fixture source\n"
            finally:
                acquisition._git(resolved.checkout, "worktree", "remove", str(worktree))
            marker = resolved.checkout / ".git" / "runtime-source-owner.json"
        owner = json.loads(marker.read_text())
        owner["repo"] = str(tmp_path / "different-repository")
        marker.write_text(json.dumps(owner))
        with pytest.raises(SourceAcquisitionError, match="different selection owner"):
            with acquisition.acquire(RuntimeSource("main", str(repository)), root=cache, compact=True):
                pytest.fail("compact-key collision admitted a different repository owner")
        readonly = cache / "readonly-object"
        readonly.write_bytes(b"fixture Git object\n")
        readonly.chmod(0o400)
    assert not cache.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows long interpreter-path creation")
def test_native_toolchain_query_from_a_deep_preparation_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    query_versions = importlib.import_module("build_toolchain_queries").query_versions

    uv = shutil.which("uv")
    assert uv is not None
    environment = tmp_path / ("deep-output-" + "x" * 100) / ("toolchain-" + "y" * 100)
    result = subprocess.run(
        [uv, "venv", "--python", sys.executable, str(environment)],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    python = environment / "Scripts" / "python.exe"
    assert len(str(python)) > 260
    assert query_versions(python, (), dict(os.environ), deadline=time.monotonic() + 30) == {}
    marker = importlib.import_module("build_toolchain_lock")._query_marker_environment(
        python, deadline=time.monotonic() + 30,
    )
    assert marker["sys_platform"] == "win32"
    application = environment / "application"
    application.mkdir()
    (application / "pyproject.toml").write_text(
        '[project]\nname="fixture-application"\nversion="0"\nrequires-python=">=3.10"\n'
        'dependencies=[]\n[tool.uv]\npackage=false\n',
    )
    importlib.import_module("local_install_profile")._lock_application(
        application, uv=uv, deadline=time.monotonic() + 30,
    )
    assert (application / "uv.lock").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows application-lock path spelling")
@pytest.mark.parametrize("spelling", ["ordinary", "extended", "deep-extended"])
def test_native_application_lock_remains_copyable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    profile = importlib.import_module("local_install_profile")
    uv = shutil.which("uv")
    assert uv is not None
    app = tmp_path / ("deep-" + "x" * 140 if spelling == "deep-extended" else "prepared")
    app = app / "profiles" / "manager" / "snapshot" / "applications" / "manager"
    (app / "wheels").mkdir(parents=True)
    wheel = "fixture-1.0-py3-none-any.whl"
    with zipfile.ZipFile(app / "wheels" / wheel, "w") as archive:
        archive.writestr(
            "fixture-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n\n",
        )
        archive.writestr(
            "fixture-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
        )
        archive.writestr("fixture-1.0.dist-info/RECORD", "")
    (app / "pyproject.toml").write_text(
        '[project]\nname="fixture-application"\nversion="0"\nrequires-python=">=3.10"\n'
        'dependencies=["fixture==1.0"]\n[tool.uv]\npackage=false\n'
        f'[tool.uv.sources]\nfixture={{path="wheels/{wheel}"}}\n',
    )
    selected = app if spelling == "ordinary" else Path("\\\\?\\" + str(app))
    profile._lock_application(selected, uv=uv, deadline=time.monotonic() + 30)
    relocated = tmp_path / "relocated"
    shutil.copytree(app, relocated)
    result = subprocess.run(
        [uv, "lock", "--check", "--offline", "--python", sys.executable, "--project", str(relocated)],
        capture_output=True, text=True, timeout=30, cwd=Path(tmp_path.anchor),
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
