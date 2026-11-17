"""Export consumes canonical preparation and preserves the slot setup boundary."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import application_footprint as footprint
import local_install_profile as profiles
import install_source as source_inputs
import installer_engine as engine
from installer_dependency_config import native_retained_path


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[str]]:
    snapshot = tmp_path / "prepared"
    application = snapshot / "applications" / "worktree-manager"
    (application / "wheels").mkdir(parents=True)
    record = {
        "name": "worktree-manager", "commands": ["worktree-manager"],
        "root_environment": "WORKTREE_MANAGER_ROOT",
        "construction": {"runtime_version": "1.0+local.abc"},
    }
    for name, content in {
        "application.json": json.dumps(record),
        ".python-version": "3.14\n", "pyproject.toml": "[project]\n",
        "uv.lock": "version = 1\n",
        "install.sh": '#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n',
        "install.ps1": "ConvertTo-Json -InputObject @($args) -Compress\nexit 7\n",
        "install.cmd": "@echo off\r\nexit /b 7\r\n",
    }.items():
        path = application / name
        path.write_text(content, encoding="utf-8", newline="")
        if path.suffix == ".sh":
            path.chmod(0o700)
    (application / "wheels" / "manager.whl").write_bytes(b"fixture wheel bytes")
    events: list[str] = []
    selected = SimpleNamespace(
        project={"application": "applications/worktree-manager"}, record={},
    )
    inputs = SimpleNamespace(verify=lambda: events.append("retained-inputs"))
    @contextmanager
    def retain(
        path: Path, component: str, *, deadline: float,
    ) -> Iterator[tuple[SimpleNamespace, SimpleNamespace]]:
        assert path == snapshot and component == "worktree-manager"
        assert deadline > time.monotonic()
        events.append("open")
        try:
            yield selected, inputs
        finally:
            events.append("close")
    monkeypatch.setattr(source_inputs, "project_inputs", retain)
    monkeypatch.setattr(profiles, "_verify_application", lambda *_args: events.append("canonical-profile"))
    monkeypatch.setattr(engine, "verify_application_wheels", lambda *_args: events.append("copied-wheels"))
    return snapshot, application, events


def test_export_retains_canonical_inputs_and_copies_a_complete_relocatable_application(
    tmp_path: Path, prepared: tuple[Path, Path, list[str]],
) -> None:
    snapshot, application, events = prepared
    destination = tmp_path / "footprint"
    footprint.export({"worktree-manager": snapshot}, destination, deadline=time.monotonic() + 30)
    exported = destination / "apps" / "worktree-manager"
    for path in application.rglob("*"):
        if path.is_file():
            assert (exported / path.relative_to(application)).read_bytes() == path.read_bytes()
    assert events == ["open", "canonical-profile", "copied-wheels", "retained-inputs", "close"]
    assert json.loads((destination / "footprint.json").read_text(encoding="utf-8"))["apps"] == [{
        "name": "worktree-manager", "version": "1.0+local.abc", "dependencies": [],
    }]
    assert not (exported / ".venv").exists()
    assert not (exported / "state").exists()
    shutil.rmtree(snapshot)
    moved = tmp_path / "moved footprint"
    destination.rename(moved)
    entry = moved / "apps" / "worktree-manager" / "bin" / (
        "setup.ps1" if os.name == "nt" else "setup.sh"
    )
    shell = shutil.which("pwsh" if os.name == "nt" else "sh")
    assert shell is not None
    arguments = ["--stage", "", "two words", 'a"quote']
    result = subprocess.run(
        [shell, *(["-NoProfile", "-File"] if os.name == "nt" else []), str(entry), *arguments],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 7, (result.stdout, result.stderr)
    observed = json.loads(result.stdout) if os.name == "nt" else result.stdout.splitlines()
    assert observed == arguments


@pytest.mark.parametrize("damage", ["state", ".venv", "python", "indirect"])
def test_invalid_prepared_application_never_publishes_a_footprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    prepared: tuple[Path, Path, list[str]], damage: str,
) -> None:
    snapshot, application, events = prepared
    if damage in {"state", ".venv"}:
        (application / damage).mkdir()
    elif damage == "python":
        (application / ".python-version").unlink()
    else:
        target = application / "wheels" / "manager.whl"
        original = Path.lstat
        def indirect(path: Path, *args: object, **kwargs: object) -> object:
            info = original(path)
            if path == target:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400,
                )
            return info
        monkeypatch.setattr(Path, "lstat", indirect)
    destination = tmp_path / "footprint"
    with pytest.raises(ValueError):
        footprint.export({"worktree-manager": snapshot}, destination, deadline=time.monotonic() + 30)
    assert not destination.exists()
    assert events[-1] == "close"


@pytest.mark.parametrize("branch", ["dev", "main"])
def test_cli_delegates_the_exact_source_and_one_budget_to_canonical_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    prepared: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str],
    branch: str,
) -> None:
    import local_install_source

    snapshot, _application, _events = prepared
    output = tmp_path / "output"
    seen: list[dict[str, Any]] = []
    def prepare(
        repository: Path, commit: str, main_head: str, destination: Path, **options: Any,
    ) -> dict[str, str]:
        assert repository == tmp_path and commit == "a" * 40 and main_head == "b" * 40
        assert destination == output
        destination.mkdir()
        seen.append(options)
        return {"worktree-manager": str(snapshot)}
    monkeypatch.setattr(local_install_source, "prepare_applications", prepare)
    assert footprint.main([
        "--source-checkout", str(tmp_path), "--source-commit", "a" * 40,
        "--main-head", "b" * 40, "--branch", branch, "--output", str(output), "--timeout", "30",
    ]) == 0
    assert seen[0]["components"] == ("worktree-manager",)
    assert seen[0]["branch"] == branch
    assert seen[0]["uv"] == shutil.which("uv")
    assert 0 < seen[0]["deadline"] - time.monotonic() <= 30
    assert json.loads(capsys.readouterr().out) == {"footprint": str(output / "footprint")}


@pytest.mark.parametrize("options", [("--branch", "wip"), ("--branch", "dev", "--dev-head", "c" * 40)])
def test_cli_requires_explicit_dev_seed_only_for_frozen_wip(
    tmp_path: Path, options: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit) as error:
        footprint.main([
            "--source-checkout", str(tmp_path), "--source-commit", "a" * 40,
            "--main-head", "b" * 40, "--output", str(tmp_path / "output"), *options,
        ])
    assert error.value.code == 2
    assert not (tmp_path / "output").exists()


def test_isolated_cli_help_loads_owned_tools_without_caller_imports(tmp_path: Path) -> None:
    (tmp_path / "local_install_source.py").write_text('raise AssertionError("foreign caller")\n')
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(TOOLS / "application_footprint.py"), "--help"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "--source-commit" in result.stdout


def test_export_is_accepted_by_the_real_manager_planner_after_source_removal(
    tmp_path: Path, prepared: tuple[Path, Path, list[str]],
) -> None:
    sys.path.insert(0, str(TOOLS.parent / "worktree-manager" / "src"))
    from worktree_manager.application_footprints import plan

    snapshot, _application, _events = prepared
    destination = tmp_path / "footprint"
    footprint.export({"worktree-manager": snapshot}, destination, deadline=time.monotonic() + 30)
    shutil.rmtree(snapshot)
    moved = tmp_path / "relocated"
    destination.rename(moved)
    root = tmp_path / "apps"
    applications = plan(moved, root)
    assert len(applications) == 1
    assert applications[0].slot == root / "worktree-manager" / "versions" / "1.0+local.abc"
    assert applications[0].source == moved / "apps" / "worktree-manager"
    assert not root.exists()


@pytest.fixture
def frozen_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=", "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", *args],
            cwd=repository, check=True, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip()
    git("init", "--initial-branch=dev")
    (repository / "input.txt").write_text("Dev\n", encoding="utf-8")
    git("add", "input.txt")
    git("commit", "-m", "Dev fixture")
    dev = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/dev", dev)
    (repository / "input.txt").write_text("frozen WIP\n", encoding="utf-8")
    git("commit", "-am", "Frozen fixture")
    commit = git("rev-parse", "HEAD")
    (repository / "input.txt").write_text("unselected dirty bytes\n", encoding="utf-8")
    return repository, dev, commit


def test_frozen_wip_uses_only_exact_commit_and_shared_preparation_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_repo: tuple[Path, str, str],
) -> None:
    import local_install_source as preparation
    import prepare_runtime_source as source

    repository, dev, commit = frozen_repo
    output = tmp_path / "prepared"
    deadline = time.monotonic() + 60
    seen: list[str] = []
    def compose(
        repo: Path, selected: str, main: str, built: Path, **options: Any,
    ) -> dict[str, Any]:
        assert repo == repository and selected == commit and main == dev
        assert options == {"projects": ("standalone:worktree-manager",), "deadline": deadline}
        assert source._git(repo, "show", selected + ":input.txt", deadline=deadline) == "frozen WIP"
        assert built == output / "inputs"
        seen.append("compose")
        return {"source_commit": selected}
    @contextmanager
    def build(built: Path, record: dict[str, Any], **options: Any) -> Iterator[tuple[Any, Any]]:
        assert built == output / "inputs" and record["source_commit"] == commit
        assert options == {
            "projects": ("standalone:worktree-manager",), "uv": "fixture-uv",
            "deadline": deadline, "retain_toolchain": True,
        }
        seen.append("build")
        yield record, "toolchain"
        seen.append("settled")
    def profiles(
        built: Path, record: dict[str, Any], target: Path,
        components: tuple[str, ...], extras: dict[str, Any], toolchain: Any, **options: Any,
    ) -> dict[str, str]:
        assert target == output and components == ("worktree-manager",) and extras == {}
        assert toolchain == "toolchain" and options == {"uv": "fixture-uv", "deadline": deadline}
        seen.append("profile")
        return {"worktree-manager": str(output / "snapshot")}
    monkeypatch.setattr(preparation, "compose", compose)
    monkeypatch.setattr(preparation, "_built_projects", build)
    monkeypatch.setattr(preparation, "_prepare_profiles", profiles)
    monkeypatch.setattr(preparation, "_application_completion", lambda *_args, **_kw: {"fixture": True})
    result = footprint.prepare_frozen(
        repository, commit, dev, dev, output, uv="fixture-uv", deadline=deadline,
    )
    assert result == {"worktree-manager": str(output / "snapshot")}
    assert seen == ["compose", "build", "profile", "settled"]
    assert json.loads((output / "applications.json").read_text()) == {"fixture": True}
    assert (repository / "input.txt").read_text() == "unselected dirty bytes\n"


@pytest.mark.parametrize("damage", ["not-dev-ancestor", "not-fetched-dev", "abbreviated"])
def test_frozen_wip_rejects_unrelated_or_implicit_inputs_before_output(
    tmp_path: Path, frozen_repo: tuple[Path, str, str], damage: str,
) -> None:
    repository, dev, commit = frozen_repo
    selected, seed = (dev, commit) if damage == "not-dev-ancestor" else (commit, dev)
    if damage == "not-dev-ancestor":
        import prepare_runtime_source as source
        source._git(
            repository, "update-ref", "refs/remotes/origin/dev", commit,
            deadline=time.monotonic() + 30,
        )
    elif damage == "not-fetched-dev":
        seed = commit
    elif damage == "abbreviated":
        selected = commit[:12]
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        footprint.prepare_frozen(
            repository, selected, dev, seed, output,
            uv="fixture-uv", deadline=time.monotonic() + 30,
        )
    assert not output.exists()


@pytest.mark.skipif(
    not os.environ.get("MANAGER_PREPARED_FOOTPRINT"),
    reason="requires an explicitly prepared real Manager footprint",
)
def test_actual_prepared_manager_stages_without_changing_legacy_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sys.path.insert(0, str(TOOLS.parent / "worktree-manager" / "src"))
    from worktree_manager.application_footprints import install, plan

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    legacy = home / ".worktree-manager"
    legacy.mkdir()
    marker = legacy / "current-version"
    marker.write_bytes(b"legacy\n")
    command = home / ".local" / "bin" / "worktree-manager.ps1"
    command.parent.mkdir(parents=True)
    command.write_bytes(b"legacy command\n")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(legacy))
    source = Path(os.environ["MANAGER_PREPARED_FOOTPRINT"])
    applications = plan(source, home / ".copilot-extensions" / "apps")
    assert len(applications) == 1 and applications[0].name == "worktree-manager"
    deadline = time.monotonic() + 180
    install(applications, stage=True, deadline=deadline)
    application = applications[0]
    assert (application.slot / ".venv" / "pyvenv.cfg").is_file()
    assert not (application.root / "current-version").exists()
    assert marker.read_bytes() == b"legacy\n"
    assert command.read_bytes() == b"legacy command\n"
    python = application.slot / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    native: Callable[[Path], str] = native_retained_path
    result = subprocess.run(
        [native(python), "-I", "-B", "-c",
         "import importlib.util,pathlib,sys\n"
         "slot=pathlib.Path(sys.prefix).parent\n"
         "spec=importlib.util.spec_from_file_location('fixture_install',slot/'install.py')\n"
         "module=importlib.util.module_from_spec(spec)\n"
         "sys.modules[spec.name]=module\n"
         "spec.loader.exec_module(module)\n"
         "with module._qualified(slot,module._application(slot)) as (_,unchanged):\n"
         "    unchanged()\n"],
        executable=native(python),
        cwd=home, env={**os.environ, "WORKTREE_MANAGER_ROOT": str(application.root)},
        capture_output=True, text=True, timeout=max(1, deadline - time.monotonic()),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (application.slot / ".install-complete.json").is_file()
    assert not (application.root / "current-version").exists()
    assert marker.read_bytes() == b"legacy\n"
    assert command.read_bytes() == b"legacy command\n"
    install(applications, stage=True, deadline=deadline)
    assert not (application.root / "current-version").exists()
    assert marker.read_bytes() == b"legacy\n"
    assert command.read_bytes() == b"legacy command\n"
