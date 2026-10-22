"""Actual retained registration/removal callbacks without uv or source imports."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manager_runtime_install import SOURCE, _environment, _prepare_command, _run
from worktree_manager import mux_daemon, self_install as si

BASELINE = "32d48a3e5dccdbbf8b4a2d6f2d856fe2ddfdbe86"


def prepare(directory):
    directory.mkdir(exist_ok=True)
    env = _environment(directory)
    root = directory / "root with spaces"
    root.mkdir()
    (root / "current-version").write_text("1.0.0\n")
    payload = directory / "input"
    si._copy_payload(SOURCE, payload)
    result = _prepare_command([
        getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
        payload / "scripts" / "runtime_install.py", "--root", root,
        "--payload", payload, "--", "--version",
    ], env, directory)
    assert result.returncode == 0, result.stdout + result.stderr
    assert si.current_version(root) != "1.0.0"
    assert not (root / "versions" / "1.0.0").exists()
    return {"directory": str(directory), "root": str(root),
            "slot": str(si.version_slot(si.current_version(root), root))}


@pytest.fixture(scope="module")
def installed():
    return json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text())


def _script(slot, name, env, directory):
    if os.environ.get("MANAGER_CALLBACK_BASELINE") == "1":
        result = _run(["git", "-C", SOURCE.parent, "show", f"{BASELINE}:worktree-manager/bin/{name}"], env, directory)
        assert result.returncode == 0, result.stderr
        return result.stdout
    return (slot / "payload" / "bin" / name).read_text(encoding="utf-8")


def _await_mapping(root, project, worktree, *, removed):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        entry = mux_daemon.get_mapping(project, worktree, root=root)
        if entry is not None and bool(entry.get("live")) != removed:
            return entry
        time.sleep(0.05)
    log = root / "mux-callback.log"
    pytest.fail(f"callback did not {'remove' if removed else 'register'} mapping: "
                + (log.read_text(encoding="utf-8") if log.exists() else "no callback log"))


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
        pytest.fail("healthy constructed slot must be reused, not rebuilt")

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


@pytest.mark.parametrize("shell", ["ps1", "sh"] if os.name == "nt" else ["sh"])
def test_actual_retained_register_and_remove_without_uv(installed, tmp_path, monkeypatch, shell):
    directory, root, slot = (Path(installed[key]) for key in ("directory", "root", "slot"))
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
        launch = _script(slot, f"launch-session.{shell}", env, directory)
        pane = _script(slot, f"pane-wrapper.{shell}", env, directory)
        bin_path = slot / "payload" / "bin"
        if shell == "sh":
            bash = Path(shutil.which("git")).parent.parent / "bin" / "bash.exe" if os.name == "nt" else shutil.which("bash")
            env["PATH"] = "/usr/bin:/bin"
            function = launch.split("        _aw_publish_managed_mux_live() {", 1)[1].split("\n        }\n", 1)[0]
            register = (
                '_aw_publish_managed_mux_live() {' + function + "\n}\n"
                'tmux() { printf "%s\\n" "fixture:1"; }\n'
                f'LAUNCH_PROJECT={project}; WORKTREE_ID={worktree}; '
                f'_aw_publish_managed_mux_live "{session}" "{directory.as_posix()}"'
            )
            start = pane.index("\nif command -v uv") if "\nif command -v uv" in pane else pane.index('\nif [[ -n "$AW_PROJECT"')
            removal = pane[start:pane.index("\n# Intentional interrupt", start)]
            remove = (
                'callback() {\n' + removal + '\n}\n'
                'tmux() { printf "%s\\n" "fixture:1"; }\n'
                f'AW_PROJECT={project}; AW_WT={worktree}; AW_MUX_SESSION={session}; callback'
            )
            invoke = lambda code, name: _run([bash, "-c", code, (bin_path / name).as_posix()], env, directory)
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
            start = pane.index("\nif (\n    -not [string]::IsNullOrWhiteSpace($awProject)")
            removal = pane[start:pane.index("\n# Intentional interrupt", start)]
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
                header + f"$awProject='{project}'; $awWt='{worktree}'; $awMuxSession='{session}';"
                "$awPsmuxBin='psmux';\n" + removal, encoding="utf-8",
            )
            removed = _run([powershell, "-NoProfile", "-NonInteractive", "-File", script], env, directory)
        assert removed.returncode == 0, removed.stderr
        entry = _await_mapping(root, project, worktree, removed=True)
        assert entry["mux_session"] == session
        assert entry["session_incarnation"] == "fixture:1"
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    destination = parser.parse_args().prepare
    (destination / "fixtures.json").write_text(json.dumps(prepare(destination)), encoding="utf-8")
