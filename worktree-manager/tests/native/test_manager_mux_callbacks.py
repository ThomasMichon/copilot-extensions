"""Promoted Manager staging and retained callbacks without source-backed launch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manager_runtime_install import SOURCE, _environment, _prepare_command, _promoted_payload, _run
from worktree_manager import mux_daemon, self_install as si

BASELINE = "32d48a3e5dccdbbf8b4a2d6f2d856fe2ddfdbe86"


def _callback_boundary(directory, root, slot, env):
    if os.name != "nt":
        return None
    from agent_procutil import _daemon_instance_owner as owner

    parent_user, parent_owner = owner._current_sids(os.getpid())
    python = si._runtime_installer().runtime.slot_python(root, slot.name)
    bash = Path(shutil.which("git")).parent.parent / "bin" / "bash.exe"
    code = (
        "import hashlib,json,os,sys; from pathlib import Path; "
        "from agent_procutil import _daemon_instance_owner as owner; "
        "user,default_owner=owner._current_sids(os.getpid()); "
        "print(json.dumps({'user':user,'owner':default_owner,'prefix':sys.prefix,"
        "'package':owner.__file__,"
        "'sdk_hash':hashlib.sha256(Path(owner.__file__).read_bytes()).hexdigest()}))"
    )
    process = _run([
        bash, "-c", 'exec "$@"', "manager-identity-probe",
        python.as_posix(), "-I", "-B", "-c", code,
    ], env, directory)
    assert process.returncode == 0, process.stderr
    child = json.loads(process.stdout)
    same_user = child["user"] == parent_user
    installed = (
        Path(child["prefix"]).resolve().is_relative_to(slot.resolve())
        and Path(child["package"]).resolve().is_relative_to(Path(child["prefix"]).resolve())
    )
    assert same_user and installed, "Bash identity probe escaped the admitted user/runtime"
    expected = SOURCE.parent / "libs" / "agent-procutil" / "src" / "agent_procutil" / "_daemon_instance_owner.py"
    assert child["sdk_hash"] == hashlib.sha256(expected.read_bytes()).hexdigest()
    result = {
        "same_token_user": same_user,
        "different_token_owner": child["owner"] != parent_owner,
        "parent_owner_is_token_user": parent_owner == parent_user,
        "child_owner_is_token_user": child["owner"] == child["user"],
        "installed_sdk_matches_source": installed,
        "sdk_owner_sha256": child["sdk_hash"],
        "runtime_archive_sha256": hashlib.sha256(
            (slot / "payload" / "scripts" / "daemon_instance_runtime.pyz").read_bytes()
        ).hexdigest(),
    }
    print("Manager callback token boundary: " + json.dumps(result, sort_keys=True), flush=True)
    return result


def prepare(directory):
    directory.mkdir(exist_ok=True)
    env = _environment(directory)
    root = directory / "root with spaces"
    root.mkdir()
    (root / "current-version").write_text("1.0.0\n")
    payload, promotion = _promoted_payload(directory)
    result = _prepare_command([
        getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
        payload / "scripts" / "runtime_install.py", "--root", root,
        "--payload", payload, "--", "--version",
    ], env, directory)
    assert result.returncode == 0, result.stdout + result.stderr
    assert si.current_version(root) != "1.0.0"
    assert not (root / "versions" / "1.0.0").exists()
    slot = si.version_slot(si.current_version(root), root)
    original, returned = directory / "original-copy", slot / "payload"
    si._copy_payload(payload, original)
    engine = si._runtime_installer().engine

    def contents(project):
        return {
            path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in engine._source_files(engine._package_roots(project))
        }

    first, second = contents(original), contents(returned)
    difference = {
        "added": sorted(second.keys() - first.keys()),
        "removed": sorted(first.keys() - second.keys()),
        "changed": sorted(name for name in first.keys() & second.keys() if first[name] != second[name]),
    }
    assert not any(difference.values()), "Promoted source changed during construction: " + json.dumps(difference)
    return {"directory": str(directory), "root": str(root),
            "slot": str(slot), "source-roundtrip": difference,
            "promotion": promotion, "callback_boundary": _callback_boundary(directory, root, slot, env)}


@pytest.fixture
def installed():
    record = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())
    root = Path(record["root"])
    selectors = {root / name: (root / name).read_bytes() for name in ("current-version", "last-known-good")}
    try:
        yield record
    finally:
        for path, content in selectors.items():
            path.write_bytes(content)


def _script(slot, name, env, directory, *, payload=None):
    if os.environ.get("MANAGER_CALLBACK_BASELINE") == "1":
        result = _run(["git", "-C", SOURCE.parent, "show", f"{BASELINE}:worktree-manager/bin/{name}"], env, directory)
        assert result.returncode == 0, result.stderr
        return result.stdout
    return ((payload or slot / "payload") / "bin" / name).read_text(encoding="utf-8")


def _await_mapping(root, project, worktree, *, removed):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        entry = mux_daemon.get_mapping(project, worktree, root=root)
        if entry is not None and bool(entry.get("live")) != removed:
            return entry
        time.sleep(0.05)
    logs = "\n".join(
        name + ":\n" + (root / name).read_text(encoding="utf-8")[-8192:]
        for name in ("mux-callback.log", "mux-daemon.log") if (root / name).exists()
    )
    pytest.fail(f"callback did not {'remove' if removed else 'register'} mapping: "
                + (logs or "no callback log"))


@pytest.mark.parametrize("selector_state", ["missing", "blank", "whitespace"])
def test_missing_selector_is_planned_then_reuses_healthy_constructed_slot(
    installed, monkeypatch, selector_state,
):
    directory, root, slot = (Path(installed[key]) for key in ("directory", "root", "slot"))
    env = _environment(directory)
    for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
        monkeypatch.setenv(name, env[name])
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    payload = slot / "payload"
    version = si.payload_version(payload)
    assert si.needs_install(version, root) is False
    files = [slot / ".install-complete.json", slot / ".runtime-input-origins.json",
             slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")]
    before = {path: (path.stat().st_ino, path.read_bytes()) for path in files}
    bin_dir = Path(env["HOME"]) / ".local" / "bin"
    if os.name == "nt":
        launch = [os.environ["COMSPEC"], "/d", "/c", bin_dir / "worktree-manager.cmd", "--version"]
    else:
        launch = [shutil.which("bash"), bin_dir / "worktree-manager", "--version"]
    launch_env = dict(env, WORKTREE_MANAGER_ROOT=str(root), UV_OFFLINE="1",
                      PATH="" if os.name == "nt" else "/usr/bin:/bin")
    marker = root / "current-version"
    last_good = root / "last-known-good"
    if selector_state == "missing":
        marker.unlink()
    elif selector_state == "blank":
        marker.write_bytes(b"")
        last_good.write_bytes(b"")
    else:
        marker.write_bytes(b"\t \r\n")
        last_good.write_bytes(b"\r\n\t ")
    selectors = {path: path.read_bytes() if path.exists() else None for path in (marker, last_good)}
    assert _run(launch, launch_env, directory).returncode != 0
    assert si.needs_install(version, root) is True
    assert si.self_install(payload, root=root, dry_run=True).action == "planned"
    assert {path: path.read_bytes() if path.exists() else None for path in selectors} == selectors
    adapter = si._runtime_installer()

    def unexpected_construction(*args, **kwargs):
        pytest.fail("healthy constructed slot must be reused, not rebuilt: "
                    + json.dumps(installed["source-roundtrip"], sort_keys=True))

    monkeypatch.setattr(adapter.engine, "_construct", unexpected_construction)
    result = si.self_install(payload, root=root, dry_run=False)
    assert result.action == "installed", result
    assert Path(result.slot) == slot
    assert marker.read_text().strip() == slot.name
    assert last_good.read_text().strip() == slot.name
    assert si.needs_install(version, root) is False
    assert all((path.stat().st_ino, path.read_bytes()) == value for path, value in before.items())
    launched = _run(launch, launch_env, directory)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout.strip() == f"worktree-manager {version}"


def test_stage_only_proves_installed_passive_readiness_without_publication_or_rebuild(
    installed, monkeypatch,
):
    directory, root, slot = (Path(installed[key]) for key in ("directory", "root", "slot"))
    env = _environment(directory)
    for name in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"):
        monkeypatch.setenv(name, env[name])
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    adapter = si._runtime_installer()
    def files():
        return [
            root / "current-version", root / "last-known-good",
            slot / ".install-complete.json", slot / ".runtime-input-origins.json",
            *(path for path in (Path(env["HOME"]) / ".local" / "bin").iterdir() if path.is_file()),
            *(path for path in Path(env["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"]).iterdir()
              if path.is_file()),
        ]

    before = {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()) for path in files()}
    routing = root / "mux-daemon-routing"
    routing_before = {path: path.read_bytes() for path in routing.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("staging rebuilt source, published commands or activated a runtime")

    monkeypatch.setattr(adapter.engine, "_construct", forbidden)
    monkeypatch.setattr(adapter.engine, "_retained_inputs", forbidden)
    monkeypatch.setattr(adapter.engine, "retained_source_wheels", forbidden)
    monkeypatch.setattr(si, "_deploy_binstubs", forbidden)
    monkeypatch.setattr(si, "_write_control_plane_provider_manifest", forbidden)
    monkeypatch.setattr(si, "_complete_candidate", forbidden)
    result = si.self_install(slot / "payload", root=root, dry_run=False, stage_only=True)
    assert result.action == "staged", result.reason
    assert Path(result.slot) == slot
    assert {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()) for path in files()} == before
    assert {path: path.read_bytes() for path in routing.rglob("*") if path.is_file()} == routing_before


def exercise_retained_callbacks(installed, tmp_path, monkeypatch, shell):
    directory, root, slot = (Path(installed[key]) for key in ("directory", "root", "slot"))
    payload = Path(installed.get("payload", slot / "payload"))
    env = _environment(directory)
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "WORKTREE_MANAGER_ENGINE_CMD", "WORKTREE_MANAGER_ENGINE_ARGV"):
        env.pop(name, None)
    env["AGENT_HOME"] = env["HOME"]
    env["WORKTREE_MANAGER_ROOT"] = str(root)
    env["UV_OFFLINE"] = "1"
    fake_engine = tmp_path / "engine.py"
    fake_engine.write_text("import json; print(json.dumps({'ok':True,'applied':True}))\n")
    env["WORKTREE_MANAGER_ENGINE_ARGV"] = json.dumps([sys.executable, "-I", str(fake_engine)])
    project, worktree, session = "callback-fixture", f"worktree-{shell}", f"session-{shell}"
    runtime = mux_daemon.MuxDaemonRuntime(mux_daemon.registry_path(root), root=root, passive=True)
    runtime.start()
    mux_daemon.write_lock_data(mux_daemon.lock_path(root), runtime.lock_extra())
    try:
        launch = _script(slot, f"launch-session.{shell}", env, directory, payload=payload)
        pane = _script(slot, f"pane-wrapper.{shell}", env, directory, payload=payload)
        bin_path = payload / "bin"
        if shell == "sh":
            bash = Path(shutil.which("git")).parent.parent / "bin" / "bash.exe" if os.name == "nt" else shutil.which("bash")
            env["PATH"] = "/usr/bin:/bin"
            mux_bin = tmp_path / "mux-bin"
            mux_bin.mkdir()
            tmux = mux_bin / "tmux"
            tmux.write_text("#!/bin/sh\nprintf '%s\\n' 'fixture:1'\n", encoding="utf-8")
            tmux.chmod(0o755)
            env["FIXTURE_MUX_BIN"] = str(mux_bin)
            mux_path = (
                'if command -v cygpath >/dev/null 2>&1; then '
                'export PATH="$(cygpath -u "$FIXTURE_MUX_BIN"):$PATH"; '
                'else export PATH="$FIXTURE_MUX_BIN:$PATH"; fi\n'
            )
            function = launch.split("        _aw_publish_managed_mux_live() {", 1)[1].split("\n        }\n", 1)[0]
            register = (
                mux_path + '_aw_publish_managed_mux_live() {' + function + "\n}\n"
                f'LAUNCH_PROJECT={project}; WORKTREE_ID={worktree}; '
                f'_aw_publish_managed_mux_live "{session}" "{directory.as_posix()}"'
            )
            root_boundary = '\nif [[ -n "$AW_MANAGER_ROOT" ]]; then' in pane
            start = (
                pane.index('\nif [[ -n "$AW_MANAGER_ROOT" ]]; then')
                if root_boundary
                else pane.index("\nif command -v uv") if "\nif command -v uv" in pane
                else pane.index('\nif [[ -n "$AW_PROJECT"')
            )
            removal = pane[start:pane.index("\n# Intentional interrupt", start)]
            if not root_boundary and removal.rstrip().endswith("\nfi\nfi"):
                removal = removal.rstrip().rsplit("\nfi", 1)[0]
            remove = (
                mux_path + 'callback() {\n' + removal + '\n}\n'
                f"AW_MANAGER_ROOT=''; AW_PROJECT={project}; AW_WT={worktree}; AW_MUX_SESSION={session}; callback"
            )
            # Bind the extracted function's script context, like PSScriptRoot below.
            invoke = lambda code, name: _run([
                bash, "-c", code.replace("${BASH_SOURCE[0]}", "$0"), (bin_path / name).as_posix(),
            ], env, directory)
            assert _run([bash, "-c", "! command -v uv"], env, directory).returncode == 0
            registered = invoke(register, "launch-session.sh")
            assert registered.returncode == 0, registered.stderr
            _await_mapping(root, project, worktree, removed=False)
            removed = invoke(remove, "pane-wrapper.sh")
        else:
            powershell = shutil.which("pwsh")
            assert powershell
            env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
            function = launch.split("function Invoke-ManagedMuxRegister {", 1)[1].split(
                "\n# Deliver a worktree", 1,
            )[0]
            # Extracted functions acquire the harness file's automatic root;
            # bind that one context value to the real retained script location.
            function = function.replace(
                "$PSScriptRoot", "'" + str(bin_path).replace("'", "''") + "'",
            )
            root_boundary = "\nif ($awManagerRoot) {" in pane
            start = (
                pane.index("\nif ($awManagerRoot) {") if root_boundary
                else pane.index("\nif (\n    -not [string]::IsNullOrWhiteSpace($awProject)")
            )
            removal = pane[start:pane.index("\n# Intentional interrupt", start)]
            if not root_boundary and removal.rstrip().endswith("\n}\n}"):
                removal = removal.rstrip().rsplit("\n}", 1)[0]
            header = (
                f"$PSScriptRoot='{str(bin_path).replace(chr(39), chr(39)*2)}'\n"
                "function Write-SetupLog { param($message,$level); throw $message }\n"
                "function psmux { 'fixture:1' }\n"
            )
            script = tmp_path / "callback.ps1"
            script.write_text(
                header + "if (Get-Command uv -ErrorAction SilentlyContinue) { throw 'uv must be absent' }\n"
                + "function Invoke-ManagedMuxRegister {" + function
                + f"\n$script:LaunchProject='{project}'; $plan=@{{worktree_id='{worktree}'}};"
                + f"$script:AwPsmuxBin='psmux'; Invoke-ManagedMuxRegister '{session}' '{directory}'",
                encoding="utf-8",
            )
            registered = _run([powershell, "-NoProfile", "-NonInteractive", "-File", script], env, directory)
            assert registered.returncode == 0, registered.stderr
            _await_mapping(root, project, worktree, removed=False)
            script.write_text(
                header + f"$awManagerRoot=''; $awProject='{project}'; $awWt='{worktree}'; $awMuxSession='{session}';"
                "$awPsmuxBin='psmux';\n" + removal, encoding="utf-8",
            )
            removed = _run([powershell, "-NoProfile", "-NonInteractive", "-File", script], env, directory)
        assert removed.returncode == 0, removed.stderr
        entry = _await_mapping(root, project, worktree, removed=True)
        assert entry["mux_session"] == session
        assert entry["session_incarnation"] == "fixture:1"
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("shell", ["ps1", "sh"] if os.name == "nt" else ["sh"])
def test_actual_retained_register_and_remove_without_uv(installed, tmp_path, monkeypatch, shell):
    exercise_retained_callbacks(installed, tmp_path, monkeypatch, shell)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    destination = parser.parse_args().prepare
    (destination / "fixtures.json").write_text(json.dumps(prepare(destination)), encoding="utf-8")
