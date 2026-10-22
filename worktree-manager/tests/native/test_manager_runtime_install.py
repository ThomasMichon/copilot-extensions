"""Native acceptance; prepared by tools/run-manager-runtime-acceptance.py."""

from __future__ import annotations

import hashlib
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import venv
import tomllib
import tarfile
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import self_install as si

SOURCE = Path(__file__).resolve().parents[2]
LEGACY_RELEASE = "9c7a61518540787498c77a284f640c36bbe7d8e4"
LEGACY_VERSION = "0.6.35-dev1"

_LEGACY_CONTROLLER = """
import json, pathlib, sys
legacy, payload, root = map(pathlib.Path, sys.argv[1:])
sys.path[:0] = [str(legacy / 'src'), *map(str, (legacy / 'libs').rglob('src'))]
import worktree_manager
import worktree_manager.self_install as installer
assert worktree_manager.__version__ == '0.6.35-dev1'
assert pathlib.Path(installer.__file__).resolve().is_relative_to(legacy.resolve())
result = installer.self_install(payload, root=root, dry_run=False)
assert result.action == 'installed', result
print(json.dumps({'controller_version': worktree_manager.__version__, 'slot': result.slot}))
"""


def _run(argv, env, cwd):
    return subprocess.run(
        list(map(str, argv)), env=env, cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", timeout=20, **no_window_kwargs(),
    )


def _environment(directory):
    home = directory / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"] = str(home / ".agent-worktrees" / "control-plane-providers.d")
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "WORKTREE_MANAGER_ROOT"):
        env.pop(name, None)
    return env


def _prepare_command(argv, env, cwd):
    # The complete preparation phase is owned by the native group's unchanged
    # 300-second supervisor; the production engine owns each construction child.
    return subprocess.run(
        list(map(str, argv)), env=env, cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", **no_window_kwargs(),
    )


def _update_from_payload(payload, root, env, *, activation=None):
    from worktree_manager import mux_daemon_cutover

    cutovers = []
    which = shutil.which
    with pytest.MonkeyPatch.context() as patch:
        for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
            patch.setenv(name, env[name])
        patch.setenv("WORKTREE_MANAGER_ROOT", str(root))
        patch.setattr(si.shutil, "which", lambda name, *a, **k: None if name == "git" else which(name, *a, **k))
        def fetch(staging, url):
            si._clear_dir(staging)
            shutil.copytree(payload, staging / "worktree-manager", ignore=shutil.ignore_patterns(".git"))
        patch.setattr(si, "_fetch_via_tarball", fetch)
        def cutover(**kwargs):
            cutovers.append(str(kwargs["slot"]))
            assert si._slot_is_complete(kwargs["slot"])
            assert kwargs["version"] == kwargs["slot"].name
            if activation is not None:
                return activation(**kwargs)
            return {"action": "cutover", "result": {"ok": True}}
        patch.setattr(mux_daemon_cutover, "activate_after_update", cutover)
        return si.self_update(root=root, dry_run=False), cutovers


def _prepare_constructed(directory):
    directory.mkdir()
    env = _environment(directory)
    root = directory / "root"
    payload = directory / "input"
    si._copy_payload(SOURCE, payload)
    with (payload / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('\n[dependency-groups]\ndev = ["manager-dev-must-not-be-resolved==0.0.0"]\n')
    process = _prepare_command([
        getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
        payload / "scripts" / "runtime_install.py",
        "--root", root, "--payload", payload, "--", "--version",
    ], env, directory)
    assert process.returncode == 0, process.stdout + process.stderr
    slot = si.version_slot(si.current_version(root), root)
    qualified = directory / "qualified"
    si._copy_payload(SOURCE, qualified, mark_complete=False)
    source = si._runtime_installer().runtime_source
    package_version = si.payload_version(qualified)
    (qualified / "plugin.json").write_text(json.dumps({
        "name": "worktree-manager", "version": package_version,
    }), encoding="utf-8")
    for arguments in (
        ["init", "--quiet"],
        ["add", "."],
        ["-c", "user.name=Manager Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--quiet", "-m",
         "Construct isolated qualified Manager fixture\n\n"
         "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>\n"
         "Copilot-Session: 00000000-0000-4000-8000-000000000001"],
    ):
        git = _run([
            "git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
            "-c", f"core.hooksPath={qualified / '.git' / 'hooks'}",
            "-C", qualified, *arguments,
        ], env, directory)
        assert git.returncode == 0, git.stdout + git.stderr
    commit = _run(["git", "-C", qualified, "rev-parse", "HEAD"], env, directory).stdout.strip()
    tree = _run(["git", "-C", qualified, "rev-parse", "HEAD^{tree}"], env, directory).stdout.strip()
    digest = source.assembly_hash(qualified)
    generation = source.runtime_version(package_version, commit, digest)
    (qualified / "runtime-source.json").write_text(json.dumps({
        "schema": source.SOURCE_SCHEMA, "version": 1, "plugin": "worktree-manager",
        "package_version": package_version, "source_commit": commit,
        "source_tree": tree, "assembly_hash": digest, "runtime_version": generation,
    }), encoding="utf-8")
    repaired = _prepare_command([
        getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
        qualified / "scripts" / "runtime_install.py",
        "--root", root, "--payload", qualified, "--", "--version",
    ], env, directory)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    target = si.version_slot(si.current_version(root), root)
    assert target != slot and target.name == generation
    si._runtime_installer().activate(root, slot.name, expected=target.name)
    return {
        "directory": str(directory), "root": str(root), "slot": str(slot),
        "stdout": process.stdout, "returncode": process.returncode,
        "target": str(target), "qualified": str(qualified),
    }


@pytest.fixture(scope="module")
def constructed():
    prepared = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text(encoding="utf-8"))["constructed"]
    directory = Path(prepared["directory"])
    return (
        directory, _environment(directory), Path(prepared["root"]), Path(prepared["slot"]),
        SimpleNamespace(stdout=prepared["stdout"], returncode=prepared["returncode"]),
    )


def test_first_bootstrap_selects_only_installed_healthy_runtime(constructed):
    directory, env, root, slot, process = constructed
    assert (root / "current-version").read_text().strip() == slot.name
    assert (slot / ".install-complete.json").is_file()
    assert (slot / ".runtime-input-origins.json").is_file()
    assert si._slot_is_complete(slot)
    assert process.stdout.strip().endswith(f"worktree-manager {si.payload_version(SOURCE)}")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    imported = _run([
        python, "-I", "-c",
        "import json,sys,worktree_manager; "
        "from worktree_manager.relocated_launch import _relocated_launch_script; "
        "print(json.dumps([sys.prefix,worktree_manager.__file__,str(_relocated_launch_script())]))",
    ], env, directory)
    assert imported.returncode == 0, imported.stderr
    prefix, module, launcher = json.loads(imported.stdout)
    assert Path(prefix).resolve() == slot.resolve()
    assert Path(module).resolve().is_relative_to(slot.resolve())
    assert Path(launcher).is_file()
    assert Path(launcher).is_relative_to(slot / "payload" / "bin")


def _prepare_legacy_repair(directory):
    directory.mkdir()
    env = _environment(directory)
    root = directory / "root"
    archive = directory / "legacy-release.tar"
    archived = _run([
        "git", "-C", SOURCE.parent, "archive", "--format=tar", "--output", archive,
        LEGACY_RELEASE, "worktree-manager",
    ], env, directory)
    assert archived.returncode == 0, "Pinned 0.6.35 release history is required: " + archived.stderr
    extracted = directory / "release"
    extracted.mkdir()
    with tarfile.open(archive) as bundle:
        si._safe_extract(bundle, extracted)
    legacy = extracted / "worktree-manager"
    assert si.payload_version(legacy) == LEGACY_VERSION
    original = root / "versions" / LEGACY_VERSION
    original.parent.mkdir(parents=True)
    si._copy_payload(legacy, original)
    (root / "current-version").write_text(LEGACY_VERSION + "\n", encoding="utf-8")
    payload = directory / "input"
    si._copy_payload(SOURCE, payload)
    controlled = _run([
        sys.executable, "-I", "-c", _LEGACY_CONTROLLER, legacy, payload, root,
    ], dict(env, WORKTREE_MANAGER_ROOT=str(root)), directory)
    assert controlled.returncode == 0, controlled.stdout + controlled.stderr
    record = json.loads(controlled.stdout.strip().splitlines()[-1])
    assert record["controller_version"] == LEGACY_VERSION
    previous = Path(record["slot"])
    assert (root / "current-version").read_text().strip() == previous.name
    assert not (previous / ".install-complete.json").exists()
    assert not (previous / ".venv").exists()
    venv.EnvBuilder(with_pip=False).create(previous / ".venv")
    old_python = previous / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    old_hash = hashlib.sha256(old_python.read_bytes()).hexdigest()
    old_init = previous / "src" / "worktree_manager" / "__init__.py"
    old_source_hash = hashlib.sha256(old_init.read_bytes()).hexdigest()
    probe = _run([old_python, "-I", "-c", "import worktree_manager"], env, directory)
    assert probe.returncode != 0 and "No module named" in probe.stderr
    command = directory / "home" / ".local" / "bin" / (
        "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
    )
    assert "uv run --quiet --project" in command.read_text(encoding="utf-8")
    argv = [os.environ["COMSPEC"], "/d", "/c", command, "--version"] if os.name == "nt" else [
        "bash", command, "--version",
    ]
    old_launch = _run(argv, dict(env, WORKTREE_MANAGER_ROOT=str(root), UV_OFFLINE="1"), directory)
    assert old_launch.returncode != 0, old_launch.stdout
    before = (root / "current-version").read_bytes()
    process = _prepare_command([
        getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
        payload / "scripts" / "runtime_install.py",
        "--root", root, "--payload", payload, "--", "--version",
    ], env, directory)
    assert process.returncode == 0, process.stdout + process.stderr
    selected = si.version_slot(si.current_version(root), root)
    assert (root / "current-version").read_bytes() != before
    migrated = _run(argv, dict(
        env, WORKTREE_MANAGER_ROOT=str(root), UV_OFFLINE="1",
        PATH="" if os.name == "nt" else "/usr/bin:/bin",
    ), directory)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    assert migrated.stdout.strip() == f"worktree-manager {si.payload_version(payload)}"
    updated, cutovers = _update_from_payload(payload, root, env)
    assert updated.action == "already-current", updated.reason
    assert cutovers == [str(selected)]
    process = _run([
        sys.executable, "-I", "-B", SOURCE / "scripts" / "runtime_install.py",
        "--root", root, "--payload", SOURCE, "--", "--version",
    ], env, directory)
    assert process.returncode == 0, process.stdout + process.stderr
    assert si.version_slot(si.current_version(root), root) == selected
    return {
        "root": str(root), "previous": str(previous), "old_python": str(old_python),
        "old_hash": old_hash, "slot": str(selected),
        "old_source_hash": old_source_hash,
        "legacy_release": LEGACY_RELEASE, "legacy_controller": record["controller_version"],
        "old_launcher_failed": old_launch.returncode != 0, "new_launcher_succeeded": migrated.returncode == 0,
        "original_retained": original.is_dir(),
    }


@pytest.fixture(scope="module")
def legacy_repair():
    prepared = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text(encoding="utf-8"))["legacy"]
    return (
        Path(prepared["root"]), Path(prepared["previous"]), Path(prepared["old_python"]),
        prepared["old_hash"], prepared,
    )


def test_interpreter_only_selected_slot_gets_distinct_constructed_generation(legacy_repair):
    root, previous, old_python, old_hash, result = legacy_repair
    selected = Path(result["slot"])
    assert selected != previous
    assert "+repair." in selected.name
    assert (root / "current-version").read_text().strip() == selected.name
    assert si._slot_is_complete(selected)
    assert hashlib.sha256(old_python.read_bytes()).hexdigest() == old_hash
    assert hashlib.sha256((previous / "src" / "worktree_manager" / "__init__.py").read_bytes()).hexdigest() == result["old_source_hash"]
    assert not (previous / ".install-complete.json").exists()
    assert result["legacy_release"] == LEGACY_RELEASE
    assert result["legacy_controller"] == LEGACY_VERSION
    assert result["old_launcher_failed"] and result["new_launcher_succeeded"]
    assert result["original_retained"]


@pytest.mark.parametrize("shell", ["cmd", "ps1", "sh"] if os.name == "nt" else ["sh"])
def test_retained_installed_launch_never_resolves_dependencies(constructed, shell):
    directory, env, root, slot, _ = constructed
    env = dict(env, WORKTREE_MANAGER_ROOT=str(root), UV_OFFLINE="1", PATH="")
    env["__PYVENV_LAUNCHER__"] = sys.executable
    stub = directory / "home" / ".local" / "bin" / f"worktree-manager.{shell}"
    if shell == "cmd":
        argv = [os.environ["COMSPEC"], "/d", "/c", stub, "--version"]
    elif shell == "ps1":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell
        argv = [powershell, "-NoProfile", "-NonInteractive", "-File", stub, "--version"]
    else:
        stub = stub.with_suffix("")
        bash = "bash"
        if os.name == "nt":
            git = shutil.which("git")
            assert git
            bash = Path(git).parent.parent / "bin" / "bash.exe"
            assert bash.is_file()
        env["PATH"] = "/usr/bin:/bin"
        argv = [bash, stub, "--version"]
    process = _run(argv, env, directory)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == f"worktree-manager {si.payload_version(slot / 'payload')}"
    help_process = _run([*argv[:-1], "--help"], env, directory)
    assert help_process.returncode == 0, help_process.stderr
    assert "launch/resume \u00b7" in help_process.stdout
    blocked = _run(
        [*argv[:-1], "refresh-profiles", "..", "--machine", "fixture"],
        env, directory,
    )
    assert blocked.returncode == 1
    response = json.loads(blocked.stdout)
    assert response["schema"] == "copilot-extensions.terminal-profile-refresh"
    assert response["version"] == 1
    assert response["status"] == "blocked"
    assert response["error"] == "Invalid project name"


def _prepare_failed_construction(tmp_path, constructed):
    tmp_path.mkdir()
    payload = tmp_path / "payload"
    si._copy_payload(SOURCE, payload)
    project = payload / "pyproject.toml"
    original = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    project.write_text(project.read_text(encoding="utf-8").replace(
        "dependencies = [", 'dependencies = ["manager-runtime-must-not-exist==0.0.0",', 1,
    ).replace(f'version = "{original}"', 'version = "999.0.0"', 1), encoding="utf-8")
    init = payload / "src" / "worktree_manager" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8").replace(
        f'__version__ = "{si.payload_version(payload)}"', '__version__ = "999.0.0"', 1,
    ), encoding="utf-8")
    root = Path(constructed["root"])
    previous = Path(constructed["slot"])
    marker = root / "current-version"
    before = marker.read_bytes()
    receipts = {
        path: path.read_bytes() for path in (
            previous / ".install-complete.json", previous / ".runtime-input-origins.json",
            root / "last-known-good",
        )
    }
    captured = io.StringIO()
    env = _environment(tmp_path)
    with pytest.MonkeyPatch.context() as patch, redirect_stderr(captured):
        patch.setenv("UV_OFFLINE", "1")
        result, cutovers = _update_from_payload(payload, root, env)
    return {
        "action": result.action, "marker_preserved": marker.read_bytes() == before,
        "prior_retained": all(path.read_bytes() == content for path, content in receipts.items()),
        "cutovers": cutovers,
        "missing_package_reported": "manager-runtime-must-not-exist" in captured.getvalue(),
    }


def test_failed_construction_preserves_previous_selection_and_never_cuts_over():
    result = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text(encoding="utf-8"))["failed"]
    assert result["action"] == "error"
    assert result["marker_preserved"]
    assert result["prior_retained"]
    assert result["cutovers"] == []
    assert result["missing_package_reported"]


def test_mux_passive_uses_target_retained_interpreter_without_pythonpath(constructed, monkeypatch):
    directory, env, root, slot, _ = constructed
    from worktree_manager import mux_daemon_cutover as cutover

    captured = {}
    with monkeypatch.context() as patch:
        def capture(argv, **options):
            captured.update(argv=argv, options=options)
            return object()

        patch.setattr(cutover.subprocess, "Popen", capture)
        cutover.spawn_passive(slot, root=root, port=54321)
    options = captured["options"]
    assert "PYTHONPATH" not in options["env"]
    assert options["env"]["WORKTREE_MANAGER_ROOT"] == str(root)
    argv = captured["argv"]
    assert str(slot / ".runtime-bootstrap" / "runtime_launcher.py") in argv
    module_index = argv.index("worktree_manager")
    process = _run([*argv[:module_index + 1], "--version"], options["env"], directory)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == f"worktree-manager {si.payload_version(slot / 'payload')}"


@pytest.mark.parametrize("after_replace", [False, True])
def test_partial_publication_restores_native_selection_without_cutover(
    constructed, monkeypatch, after_replace,
):
    directory, env, root, previous, _ = constructed
    prepared = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())["constructed"]
    target = Path(prepared["target"])
    markers = [root / "current-version", root / "last-known-good"]
    before = {path: path.read_bytes() for path in markers}
    receipts = {
        path: path.read_bytes() for slot in (previous, target) for path in (
            slot / ".install-complete.json", slot / ".runtime-input-origins.json",
        )
    }
    publication = si._runtime_installer().runtime._runtime_component("versioned_runtime_publication")
    replace = publication.os.replace
    faults = []

    def fail_second(source, destination):
        if Path(destination) == markers[1] and not faults:
            assert markers[0].read_text().strip() == target.name
            faults.append(True)
            if after_replace:
                replace(source, destination)
            raise PermissionError("native second-selector fault")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(publication.os, "replace", fail_second)
        result, cutovers = _update_from_payload(Path(prepared["qualified"]), root, env)
    assert faults == [True]
    assert result.action == "error" and "second-selector fault" in result.reason
    assert cutovers == []
    assert all(path.read_bytes() == content for path, content in before.items())
    assert all(path.read_bytes() == content for path, content in receipts.items())


def test_same_package_generation_update_replaces_and_verifies_live_fixture_mux(constructed, monkeypatch):
    from worktree_manager import mux_daemon_cutover as cutover

    directory, env, root, previous, _ = constructed
    prepared = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())["constructed"]
    target = Path(prepared["target"])
    assert si.payload_version(previous / "payload") == si.payload_version(target / "payload")
    for slot in (previous, target):
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        probe = _run([
            python, "-I", "-c",
            "import json,sys,worktree_manager; "
            "from worktree_manager.runtime_identity import running_generation; "
            "print(json.dumps([sys.prefix,worktree_manager.__file__,running_generation()]))",
        ], env, directory)
        assert probe.returncode == 0, probe.stderr
        prefix, module, generation = json.loads(probe.stdout)
        assert Path(prefix).resolve() == slot.resolve()
        assert Path(module).resolve().is_relative_to(slot.resolve())
        assert generation == slot.name

    processes = []
    popen = cutover.subprocess.Popen

    def contained_spawn(argv, **options):
        # Exercise production argv/env, but keep test processes inside the
        # runner's Job/process tree rather than production daemon breakaway.
        options.update(no_window_kwargs())
        options.pop("start_new_session", None)
        process = popen(argv, **options)
        processes.append((argv, options, process))
        return process

    with monkeypatch.context() as patch:
        for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
            patch.setenv(name, env[name])
        patch.setattr(cutover.subprocess, "Popen", contained_spawn)
        try:
            port = cutover.pick_free_port()
            old = cutover.spawn_passive(previous, root=root, port=port)
            assert cutover._await_health("127.0.0.1", port, root=root, timeout=8, poll=0.1,
                                         generation=previous.name)
            cutover.publish_route(port, root=root, pid=old.pid, version=previous.name)
            actual_activation = cutover.activate_after_update
            result, callbacks = _update_from_payload(
                Path(prepared["qualified"]), root, env,
                activation=lambda **kwargs: actual_activation(
                    **kwargs, health_timeout=8, drain_timeout=2,
                ),
            )
            assert result.action == "updated", result
            assert callbacks == [str(target)]
            assert result.cutover["action"] == "cutover", result.cutover
            assert result.cutover["result"]["ok"], result.cutover
            route = cutover.routed_endpoint(root)
            assert route.version == target.name
            assert route.pid != old.pid
            assert cutover._health_check("127.0.0.1", route.port, root=root, generation=target.name)
            health = cutover.ControlClient(f"http://127.0.0.1:{route.port}", root=root).health()
            assert health["version"] == si.payload_version(target / "payload")
            assert health["runtime_generation"] == target.name
            assert si.current_version(root) == target.name
            target_spawns = [
                entry for entry in processes
                if str(target / ".runtime-bootstrap" / "runtime_launcher.py") in entry[0]
            ]
            assert len(target_spawns) == 1
            assert target_spawns[0][2].pid == route.pid
            assert "PYTHONPATH" not in target_spawns[0][1]["env"]
            assert cutover.activate_after_update(
                root=root, slot=target, version=target.name,
            )["reason"] == "already-active-version"
        finally:
            for _, _, process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    destination = parser.parse_args().prepare
    print("Manager acceptance: fresh bootstrap", flush=True)
    data = {"constructed": _prepare_constructed(destination / "first")}
    print("Manager acceptance: selected legacy repair", flush=True)
    data["legacy"] = _prepare_legacy_repair(destination / "legacy")
    print("Manager acceptance: failed construction rollback", flush=True)
    data["failed"] = _prepare_failed_construction(destination / "failed", data["constructed"])
    (destination / "fixtures.json").write_text(json.dumps(data), encoding="utf-8")
