"""Export consumes canonical preparation and preserves the slot setup boundary."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from collections.abc import Callable, Iterator
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
import zipfile

import pytest

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import application_footprint as footprint
import local_install_profile as profiles
import install_source as source_inputs
import installer_engine as engine
from installer_dependency_config import native_retained_path


@pytest.mark.skipif(
    not os.environ.get("MANAGER_RELEASE_INPUT_SNAPSHOT"),
    reason="requires independently admitted standalone release and native wheel inputs",
)
@pytest.mark.parametrize("complete", [False, True], ids=["missing-dependencies", "relocated"])
def test_manager_release_inputs_reach_regular_construction(
    tmp_path: Path, complete: bool,
) -> None:
    import runpy

    snapshot = Path(os.environ["MANAGER_RELEASE_INPUT_SNAPSHOT"])
    selected = source_inputs.select_project(snapshot, "worktree-manager")
    artifact = selected.project["artifact"]
    profile = source_inputs._read(source_inputs._member(snapshot, selected.project["profile"]))
    pool = source_inputs._member(snapshot, selected.project["wheel_inputs"])
    rows = profile["wheels"] if complete else artifact["wheels"]
    uv = shutil.which("uv")
    assert uv is not None
    deadline = time.monotonic() + 300

    def prepare() -> Path:
        return footprint.prepare_manager_release(
            TOOLS.parent, selected.identity.source_commit, tmp_path / "prepared",
            artifact=artifact, archives={w["filename"]: pool / w["filename"] for w in rows},
            digests={w["filename"]: w["sha256"] for w in rows}, target=profile["target"],
            policy_id=profile["approval_source"], acquisition_id=profile["acquisition_policy"],
            uv=Path(uv), deadline=deadline,
        )

    if not complete:
        with pytest.raises(profiles.ProfileError, match="incomplete applicable closure"):
            prepare()
        assert not (tmp_path / "prepared" / "footprint").exists()
        return
    exported = prepare() / "apps" / "worktree-manager"
    reader = runpy.run_path(str(exported / "install.py"))
    declaration = reader["_application"](exported)
    slot = tmp_path / "versions" / declaration["construction"]["runtime_version"]
    shutil.copytree(exported, slot)
    runtime, install_engine = reader["_resources"](slot)
    association = reader["_construction_inputs"](slot, declaration, runtime, install_engine, deadline)
    install_engine.verify_application_wheels(association.wheels, slot)
    assert association.source == selected.identity.document()
    assert len(association.origins) == len(rows)
    assert not (slot / ".venv").exists()


def _code_snapshot(slot: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(slot).as_posix(): (
            hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns,
        )
        for path in slot.rglob("*")
        if path.relative_to(slot).parts[0] != "state" and path.is_file()
    }


def _assert_code_unchanged(slot: Path, expected: dict[str, tuple[bytes, int]]) -> None:
    observed = _code_snapshot(slot)
    changed = [
        name for name in sorted(expected.keys() | observed.keys())
        if expected.get(name) != observed.get(name)
    ]
    assert not changed, changed[:20]


@contextmanager
def _manager_subscriber(endpoint: Any, root: Path) -> Iterator[None]:
    from work_coalescing_singleton import client
    from worktree_manager.mux_serving_observation import _existing_token

    token, _identity = _existing_token(root)
    identity = client.new_client_id()
    errors: list[Exception] = []
    stopped = threading.Event()

    def renew() -> None:
        while not stopped.wait(10):
            try:
                client.subscribe(endpoint.client_host, endpoint.port, token, identity)
            except Exception as error:
                errors.append(error)
                return

    client.subscribe(endpoint.client_host, endpoint.port, token, identity)
    renewal = threading.Thread(target=renew)
    renewal.start()
    try:
        yield
    finally:
        stopped.set()
        renewal.join(timeout=5)
        client.release(endpoint.client_host, endpoint.port, token, identity)
        assert not renewal.is_alive()
        assert not errors, errors


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
    monkeypatch.setattr(footprint, "_manager_setup", lambda *_args, **_kw: None)
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
@pytest.mark.parametrize("explicit_source", [False, True])
def test_cli_delegates_the_exact_source_and_one_budget_to_canonical_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    prepared: tuple[Path, Path, list[str]], capsys: pytest.CaptureFixture[str],
    branch: str, explicit_source: bool,
) -> None:
    import local_install_source

    snapshot, _application, _events = prepared
    output = tmp_path / "output"
    descriptor = {"source": "directory", "path": str(tmp_path)}
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
        *(["--source-descriptor", json.dumps(descriptor)] if explicit_source else []),
    ]) == 0
    assert seen[0]["components"] == ("worktree-manager",)
    assert seen[0]["branch"] == branch
    assert seen[0]["uv"] == shutil.which("uv")
    assert seen[0].get("source_descriptor") == (descriptor if explicit_source else None)
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


def test_source_line_classifies_ancestors_not_checkout_names(tmp_path: Path) -> None:
    import prepare_runtime_source as source

    deadline = time.monotonic() + 60
    def git(*args: str) -> str:
        return source._git(tmp_path, "-c", "user.name=Fixture", "-c",
                           "user.email=fixture@example.invalid", *args, deadline=deadline)

    git("init", "--quiet")
    tree = git("write-tree")
    base = git("commit-tree", tree, "-m", "shared base")
    main = git("commit-tree", tree, "-p", base, "-m", "release")
    main_tip = git("commit-tree", tree, "-p", main, "-m", "later release")
    dev = git("commit-tree", tree, "-p", base, "-m", "development")
    unrelated = git("commit-tree", tree, "-m", "local only")
    git("update-ref", "refs/remotes/origin/main", main_tip)
    git("update-ref", "refs/remotes/origin/dev", dev)
    assert footprint.source_line(tmp_path, main, deadline=deadline) == ("main", main_tip)
    assert footprint.source_line(tmp_path, dev, deadline=deadline) == ("dev", main_tip)
    for unknown in (base, unrelated):
        with pytest.raises(ValueError, match="does not uniquely identify"):
            footprint.source_line(tmp_path, unknown, deadline=deadline)


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


@pytest.mark.parametrize("parent_components", [False, True])
def test_frozen_wip_uses_only_exact_commit_and_shared_preparation_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_repo: tuple[Path, str, str],
    parent_components: bool,
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
    selected_output = output
    if parent_components:
        (tmp_path / "intermediate").mkdir()
        selected_output = tmp_path / "intermediate" / ".." / output.name
    result = footprint.prepare_frozen(
        repository, commit, dev, dev, selected_output, uv="fixture-uv", deadline=deadline,
    )
    assert result == {"worktree-manager": str(output / "snapshot")}
    assert seen == ["compose", "build", "profile", "settled"]
    assert json.loads((output / "applications.json").read_text()) == {"fixture": True}
    assert (repository / "input.txt").read_text() == "unselected dirty bytes\n"


def test_export_returns_normalized_directly_consumable_path(
    tmp_path: Path, prepared: tuple[Path, Path, list[str]],
) -> None:
    snapshot, _application, _events = prepared
    (tmp_path / "intermediate").mkdir()
    result = footprint.export(
        {"worktree-manager": snapshot}, tmp_path / "intermediate" / ".." / "footprint",
        deadline=time.monotonic() + 30,
    )
    assert result == tmp_path / "footprint" and ".." not in result.parts


def test_setup_assets_and_launchers_come_from_frozen_wheel_not_dirty_source(
    tmp_path: Path, frozen_repo: tuple[Path, str, str],
) -> None:
    import prepare_runtime_source as source

    repository, _dev, _commit = frozen_repo
    helper = repository / "setup-helper.py"
    helper.write_bytes((TOOLS.parent / "libs" / "installer-engine" / "runtime_application_binstubs.py").read_bytes())
    deadline = time.monotonic() + 30
    source._git(repository, "add", "setup-helper.py", deadline=deadline)
    source._git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", "Frozen helper", deadline=deadline,
    )
    commit = source._git(repository, "rev-parse", "HEAD", deadline=deadline)
    frozen = source._git(repository, "show", commit + ":setup-helper.py", deadline=deadline).encode() + b"\n"
    helper.write_bytes(b'raise AssertionError("unselected dirty helper")\n')
    application = tmp_path / "application"
    (application / "wheels").mkdir(parents=True)
    (application / "runtime").mkdir()
    (application / "runtime" / "uncommitted.py").write_bytes(b"not in frozen wheel")
    (application / ".python-version").write_text("3.14.10\n")
    (application / "application.json").write_text(json.dumps({
        "name": "worktree-manager", "commands": ["worktree-manager", "worktree-manager-install"],
    }))
    resources = "worktree_manager/_runtime_assets/scripts/"
    provider = resources + "runtime-input-provider/libs/"
    with zipfile.ZipFile(application / "wheels" / "manager.whl", "w") as archive:
        for name, content in {
            provider + "installer-engine/runtime_application_binstubs.py": frozen,
            provider + "installer-engine/runtime_application_install.py": b"# selected installer\n",
            provider + "installer-engine/installer_engine.py": b"# selected engine\n",
            provider + "versioned-runtime/versioned_runtime.py": b"# selected runtime\n",
            resources + "daemon_instance_runtime.pyz": b"selected lifecycle",
            "manager.dist-info/entry_points.txt": (
                b"[console_scripts]\nworktree-manager = worktree_manager.__main__:console_entry\n"
                b"worktree-manager-install = worktree_manager.application_footprints:main\n"
            ),
        }.items():
            archive.writestr(name, content)
    inputs = SimpleNamespace(receipt=lambda: {"distributions": {
        "copilot-extensions-worktree-manager": {"filename": "manager.whl"},
    }})
    footprint._manager_setup(application, inputs, deadline=deadline)
    assert (application / "runtime_application_binstubs.py").read_bytes() == frozen
    assert (application / "install.py").read_bytes() == b"# selected installer\n"
    assert (application / "runtime" / "installer_engine.py").read_bytes() == b"# selected engine\n"
    assert (application / "runtime" / "daemon_instance_runtime.pyz").read_bytes() == b"selected lifecycle"
    assert not (application / "runtime" / "uncommitted.py").exists()
    assert (application / "worktree-manager-install.py").is_file()
    assert helper.read_bytes() == b'raise AssertionError("unselected dirty helper")\n'


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


@pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("MANAGER_PREPARED_FOOTPRINT")
    or os.environ.get("MANAGER_NATIVE_LIFETIME") != "1",
    reason="requires an explicit native Windows Manager footprint/lifetime qualification",
)
@pytest.mark.parametrize("mode", ["repeat", "update-reverse", "default-update", "same-source-repair"])
def test_actual_prepared_manager_full_setup_and_source_unavailable_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_procutil.owned_process import run_installation_owned
    from plugin_test_environment import isolated_environment

    update_source = os.environ.get("MANAGER_UPDATE_FOOTPRINT")
    update_repository = os.environ.get("MANAGER_UPDATE_REPOSITORY")
    if mode == "update-reverse" and not update_source:
        pytest.skip("requires a second explicitly prepared Manager footprint")
    if mode in {"default-update", "same-source-repair"} and not update_repository:
        pytest.skip("requires an explicitly selected fixture Manager source repository")
    sys.path.insert(0, str(TOOLS.parent / "worktree-manager" / "src"))
    from worktree_manager import mux_daemon_cutover
    from worktree_manager.application_footprints import install, plan

    source = Path(os.environ["MANAGER_PREPARED_FOOTPRINT"])
    incoming = tmp_path / "incoming"
    shutil.copytree(source, incoming)
    environment = isolated_environment(
        dict(os.environ), tmp_path / "isolated", allow_explicit_tiers=True,
    )
    environment["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"] = str(tmp_path / "providers")
    environment["AGENT_WORKTREES_STATUS_MONITOR"] = "0"
    environment["WORKTREE_NO_UPDATE"] = "1"
    environment["AGENT_WORKTREES_NO_SELFPROVISION"] = "1"
    monkeypatch.setattr(os, "environ", environment)
    home = Path(environment["HOME"])
    legacy = home / ".worktree-manager"
    legacy.mkdir()
    marker = legacy / "current-version"
    marker.write_bytes(b"legacy\n")
    command = home / ".local" / "bin" / "worktree-manager.ps1"
    command.parent.mkdir(parents=True)
    command.write_bytes(b"legacy command\n")
    environment["WORKTREE_MANAGER_ROOT"] = str(legacy)
    applications = plan(incoming, home / ".copilot-extensions" / "apps")
    assert len(applications) == 1
    application = applications[0]
    deadline = time.monotonic() + (1500 if mode in {"default-update", "same-source-repair"} else 360)
    install(applications, stage=True, deadline=deadline)
    assert marker.read_bytes() == b"legacy\n" and command.read_bytes() == b"legacy command\n"
    assert not (application.root / "current-version").exists()
    cleanup: list[Any] = []
    with daemon_test_lifecycle(
        owner="prepared-manager-reference", registry_root=tmp_path / "daemon-instances",
        deadline=deadline, cleanup_results=cleanup,
    ) as lifecycle, ExitStack() as subscribers:
        environment.update(lifecycle)
        # This explicit lane proves survival beyond the setup caller, under the renewable lease.
        environment.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
        try:
            install(applications, stage=False, deadline=deadline)
        except subprocess.CalledProcessError as error:
            raise AssertionError((error.stdout, error.stderr)) from error
        assert (application.root / "current-version").read_text().strip() == application.version
        assert marker.read_bytes() == b"legacy\n"
        assert command.read_bytes() != b"legacy command\n"
        endpoint = mux_daemon_cutover.routed_endpoint(application.root)
        assert endpoint is not None and endpoint.version == application.version
        assert mux_daemon_cutover._health_check(
            endpoint.client_host, endpoint.port, root=application.root,
            generation=application.version,
        )
        immutable = _code_snapshot(application.slot)
        state = application.root / "state" / "fixture-user-state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_bytes(b'{"preserve":true}\n')
        incoming.rename(tmp_path / "concealed-incoming")
        installed_environment = {key: value for key, value in environment.items()
                                 if key != "WORKTREE_MANAGER_ROOT"}
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None

        def invoke(frontdoor: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
            return run_installation_owned(
                [powershell, "-NoLogo", "-NoProfile", "-File", str(frontdoor), *arguments],
                cwd=home, env=installed_environment, timeout=max(1, deadline - time.monotonic()),
                max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20,
            )

        for frontdoor, arguments in (
            (command, ["self-install"]),
            (command.parent / "worktree-manager-install.ps1", ["--help"]),
            (application.root / "bin" / "setup.ps1", []),
        ):
            result = invoke(frontdoor, arguments)
            assert result.returncode == 0, (result.stdout, result.stderr)
            if arguments == ["self-install"]:
                assert str(application.root) in result.stdout
                assert "already current" in result.stdout
        assert state.read_bytes() == b'{"preserve":true}\n'
        _assert_code_unchanged(application.slot, immutable)
        repeated_endpoint = mux_daemon_cutover.routed_endpoint(application.root)
        assert repeated_endpoint is not None and repeated_endpoint.pid == endpoint.pid
        assert mux_daemon_cutover._health_check(
            repeated_endpoint.client_host, repeated_endpoint.port, root=application.root,
            generation=application.version,
        )
        if mode in {"default-update", "same-source-repair"}:
            import update_driver
            from worktree_manager import source_config

            assert update_repository is not None
            assert Path(update_repository).is_absolute() and Path(update_repository).is_dir()
            source_config.set_source(repo=update_repository, ref="dev", root=application.root)
            installed_environment["WORKTREE_MANAGER_ROOT"] = str(application.root)
            repair_slot = None
            repair_code = None
            repair_base = None
            if mode == "same-source-repair":
                established = invoke(command, ["update", "--target", "worktree-manager"])
                assert established.returncode == 0, (established.stdout, established.stderr)
                established_endpoint = mux_daemon_cutover.routed_endpoint(application.root)
                assert established_endpoint is not None and established_endpoint.version is not None
                repair_base = established_endpoint.version
                repair_slot = application.root / "versions" / repair_base
                (repair_slot / "application.json").unlink()
                repair_code = _code_snapshot(repair_slot)
                if repair_slot == application.slot:
                    immutable = repair_code
            previous_pid = None
            default_code = None
            for attempt in range(2):
                result = invoke(command, ["update", "--target", "worktree-manager"])
                assert result.returncode == 0, (result.stdout, result.stderr)
                selected = mux_daemon_cutover.routed_endpoint(application.root)
                assert selected is not None and selected.version is not None
                assert selected.version != application.version
                if mode == "same-source-repair":
                    assert repair_base is not None
                    assert selected.version.startswith(repair_base + ".repair.")
                    assert repair_slot is not None and repair_code is not None
                    _assert_code_unchanged(repair_slot, repair_code)
                assert selected.pid is not None
                slot = application.root / "versions" / selected.version
                if attempt:
                    assert selected.pid == previous_pid and default_code is not None
                    _assert_code_unchanged(slot, default_code)
                else:
                    default_code = _code_snapshot(slot)
                previous_pid = selected.pid
                assert mux_daemon_cutover._health_check(
                    selected.client_host, selected.port, root=application.root,
                    generation=selected.version,
                )
                if not attempt:
                    subscribers.enter_context(_manager_subscriber(selected, application.root))
                    native: Callable[[Path], str] = native_retained_path
                    probe = run_installation_owned(
                        [
                            native(slot / ".venv" / "Scripts" / "python.exe"),
                            "-I", "-B", "-c",
                            "import json,sys;from pathlib import Path;"
                            "from worktree_manager import mux_daemon_cutover as c;"
                            "from worktree_manager.mux_serving_observation import _existing_token;"
                            "root=Path(sys.argv[1]).resolve();route=c.routed_endpoint(root);"
                            "token,_=_existing_token(root);"
                            "health=c.ControlClient('http://'+str(route.bind)+':'+str(route.port),"
                            "root=root,token=token).health();"
                            "print(json.dumps({'health':health,"
                            "'same_generation':c._already_at_version(route,route.version,root=root)}))",
                            str(application.root),
                        ],
                        cwd=home, env=installed_environment,
                        timeout=max(1, deadline - time.monotonic()),
                        max_stdout_bytes=1 << 20, max_stderr_bytes=1 << 20,
                    )
                    assert probe.returncode == 0, (probe.stdout, probe.stderr)
                    installed_health = json.loads(probe.stdout)
                    assert installed_health["same_generation"], installed_health
                    assert installed_health["health"]["runtime_generation"] == selected.version
                assert state.read_bytes() == b'{"preserve":true}\n'
                _assert_code_unchanged(application.slot, immutable)
                assert (slot / "application.json").is_file()
                assert (application.root / "bin" / "setup.ps1").is_file()
                assert marker.read_bytes() == b"legacy\n"
            requests = update_driver.status(root=home / ".copilot-extensions" / "update-driver")
            assert isinstance(requests, tuple) and requests
            assert all(request.state == "completed" for request in requests)
        if mode == "update-reverse":
            import update_driver

            assert update_source is not None
            execution_root = home / ".copilot-extensions" / "update-driver"
            before_requests = update_driver.status(root=execution_root)
            assert isinstance(before_requests, tuple)
            initial_self_installs = sum(
                request.spec.backend == "manager-self-install" for request in before_requests
            )
            incoming_update = tmp_path / "incoming-update"
            shutil.copytree(update_source, incoming_update)
            updated, = plan(incoming_update, application.root.parent)
            assert updated.version != application.version
            secondary = command.parent / "worktree-manager-install.ps1"
            manifest = incoming_update / "footprint.json"
            original_manifest = manifest.read_bytes()
            invalid = json.loads(original_manifest)
            invalid["apps"][0]["dependencies"] = ["missing-fixture-provider"]
            manifest.write_text(json.dumps(invalid), encoding="utf-8")
            arguments = [
                str(incoming_update), "--apps-root", str(application.root.parent), "--apply",
                "--timeout", str(max(1, deadline - time.monotonic())),
            ]
            rejected = invoke(secondary, arguments)
            assert rejected.returncode != 0
            assert not updated.slot.exists()
            assert (application.root / "current-version").read_text().strip() == application.version
            manifest.write_bytes(original_manifest)
            staged = invoke(secondary, [*arguments, "--stage"])
            assert staged.returncode == 0, (staged.stdout, staged.stderr)
            assert updated.slot.is_dir()
            assert (application.root / "current-version").read_text().strip() == application.version
            assert mux_daemon_cutover._health_check(
                repeated_endpoint.client_host, repeated_endpoint.port, root=application.root,
                generation=application.version,
            )
            incoming_pid: int | None = None
            incoming_code: dict[str, tuple[bytes, int]] | None = None
            for attempt in range(2):
                advanced = invoke(secondary, arguments)
                assert advanced.returncode == 0, (advanced.stdout, advanced.stderr)
                selected = mux_daemon_cutover.routed_endpoint(application.root)
                assert selected is not None and selected.version == updated.version
                assert selected.pid is not None
                if attempt:
                    assert selected.pid == incoming_pid
                    assert incoming_code is not None
                    _assert_code_unchanged(updated.slot, incoming_code)
                else:
                    incoming_code = _code_snapshot(updated.slot)
                incoming_pid = selected.pid
                assert mux_daemon_cutover._health_check(
                    selected.client_host, selected.port, root=application.root,
                    generation=updated.version,
                )
                assert (application.root / "current-version").read_text().strip() == updated.version
                assert state.read_bytes() == b'{"preserve":true}\n'
            reverse = invoke(secondary, [
                str(tmp_path / "concealed-incoming"), "--apps-root", str(application.root.parent),
                "--apply", "--timeout", str(max(1, deadline - time.monotonic())),
            ])
            assert reverse.returncode == 0, (reverse.stdout, reverse.stderr)
            restored = mux_daemon_cutover.routed_endpoint(application.root)
            assert restored is not None and restored.version == application.version
            assert mux_daemon_cutover._health_check(
                restored.client_host, restored.port, root=application.root,
                generation=application.version,
            )
            assert state.read_bytes() == b'{"preserve":true}\n'
            _assert_code_unchanged(application.slot, immutable)
            assert incoming_code is not None
            _assert_code_unchanged(updated.slot, incoming_code)
            assert marker.read_bytes() == b"legacy\n"
            after_requests = update_driver.status(root=execution_root)
            assert isinstance(after_requests, tuple)
            assert all(request.state == "completed" for request in after_requests)
            assert sum(
                request.spec.backend == "manager-self-install" for request in after_requests
            ) == initial_self_installs
    assert cleanup and all(result.status == "settled" for result in cleanup), cleanup
