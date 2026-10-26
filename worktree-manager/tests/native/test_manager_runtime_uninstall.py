"""Real installed removal/reinstall under the existing native acceptance owner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from contextlib import chdir
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE.parent / "tools"))

from agent_procutil.owned_process import run_owned  # noqa: E402
from plugin_test_containment import isolated_environment  # noqa: E402
from worktree_manager import self_install as si  # noqa: E402


_ORIGIN = """
import hashlib, importlib.metadata, json, pathlib, sys
import worktree_manager
from worktree_manager.runtime_candidate import verify_runtime_candidate
verify_runtime_candidate()
slot = pathlib.Path(sys.prefix).resolve()
dist = importlib.metadata.distribution('copilot-extensions-worktree-manager')
print(json.dumps({
    'prefix': str(slot), 'executable': sys.executable,
    'module': worktree_manager.__file__,
    'distribution': str(pathlib.Path(dist.locate_file('')).resolve()),
    'version': dist.version,
    'files': {name: hashlib.sha256((slot / 'payload' / name).read_bytes()).hexdigest()
              for name in ('scripts/runtime_install.py', 'scripts/runtime_uninstall.py',
                           'scripts/versioned_runtime.py', 'scripts/versioned_runtime_leases.py',
                           'src/worktree_manager/runtime_candidate.py',
                           'src/worktree_manager/self_install.py',
                           'src/worktree_manager/mux_file_lock.py',
                           'src/worktree_manager/engine_client.py',
                           'src/worktree_manager/installation_updates.py',
                           'src/worktree_manager/mux_daemon_process.py')},
    'installed_files': {
        name: hashlib.sha256((pathlib.Path(worktree_manager.__file__).parent / name).read_bytes()).hexdigest()
        for name in ('runtime_candidate.py', 'self_install.py', 'mux_file_lock.py',
                     'engine_client.py', 'installation_updates.py', 'mux_daemon_process.py')
    },
}))
"""

_DAEMON_CYCLE = """
import json, os, pathlib, subprocess, sys, time
from agent_procutil import no_window_kwargs
from worktree_manager import mux_daemon_cutover, mux_daemon_process
root = pathlib.Path(sys.argv[1])
argv = mux_daemon_process.detached_child_argv(root)
python, extra_env = mux_daemon_process.direct_daemon_python(argv[0])
env = mux_daemon_process.scrub_session_credentials(dict(os.environ))
env.pop('PYTHONDONTWRITEBYTECODE', None)
env.update(extra_env)
with subprocess.Popen(
    [python, *argv[1:]], cwd=root, env=env, stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **no_window_kwargs(),
) as child:
    try:
        deadline = time.monotonic() + 30
        endpoint = None
        while child.poll() is None and time.monotonic() < deadline:
            endpoint = mux_daemon_cutover.routed_endpoint(root)
            if endpoint is not None and endpoint.pid == child.pid:
                break
            time.sleep(0.05)
        assert endpoint is not None and endpoint.pid == child.pid, child.poll()
        client = mux_daemon_cutover.ControlClient(endpoint.base_url, root=root)
        assert client.health()
        assert client.shutdown()['shutdown']
        assert child.wait(timeout=30) == 0
        print(json.dumps({'started': True, 'stopped': True, 'pid': child.pid, 'argv': argv}))
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
"""

_LIST_QUERY = """
import json, os
from agent_procutil.owned_process import run_owned
from worktree_manager.installation_updates import configuration_pivot
os.environ.pop('PYTHONDONTWRITEBYTECODE', None)
command = list(configuration_pivot().list_cmd)
result = run_owned(command, timeout=30)
assert result.returncode == 0, result.stdout + result.stderr
inventory = json.loads(result.stdout)
assert any(row['id'] == 'worktree-manager' for row in inventory['entries'])
print(json.dumps({'manager_listed': True, 'command': command}))
"""


def _run(command, env, directory, *, timeout=180):
    with chdir(directory), patch.dict(os.environ, env, clear=True):
        try:
            return run_owned(list(map(str, command)), timeout=timeout)
        except subprocess.TimeoutExpired as error:
            print(f"Manager native command timed out: {command}", flush=True)
            print(error.stdout or "", flush=True)
            print(error.stderr or "", flush=True)
            raise


def _success(result):
    assert result.returncode == 0, (
        f"native command failed ({result.returncode}): {result.args}\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return result


def _environment(directory):
    env = isolated_environment(os.environ, directory)
    env.update({
        "WORKTREE_MANAGER_ROOT": str(directory / "root"),
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": str(directory / "providers"),
        "AGENT_WORKTREES_PROJECTS_YAML": str(directory / "state" / "projects.yaml"),
        "AGENT_WORKTREES_REPOS_YAML": str(directory / "state" / "repos.yaml"),
        "UV_CACHE_DIR": str(directory / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(directory / "uv-python"),
        "UV_TOOL_DIR": str(directory / "uv-tools"),
        "UV_PYTHON_DOWNLOADS": "never",
    })
    for name in (
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
        "COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDER",
    ):
        env.pop(name, None)
    return env


def _snapshot(roots):
    values = {}
    for root in roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if path.is_symlink():
                values[str(path)] = ("link", os.readlink(path))
            elif path.is_file():
                values[str(path)] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
            elif path.is_dir():
                values[str(path)] = ("directory", "")
    return values


def _installed_origin(slot, env, directory):
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = _success(_run([python, "-I", "-B", "-c", _ORIGIN], env, directory))
    origin = json.loads(result.stdout)
    assert Path(origin["prefix"]).resolve() == slot.resolve()
    for key in ("module", "distribution", "executable"):
        assert Path(origin[key]).absolute().is_relative_to(slot.absolute()), origin
    assert (slot / ".install-complete.json").is_file()
    assert (slot / ".runtime-input-origins.json").is_file()
    assert (slot / ".manager-artifacts.json").is_file()
    assert all(digest == origin["files"]["src/worktree_manager/" + name]
               for name, digest in origin["installed_files"].items())
    return origin


def _durable(root, env):
    return {
        root / "config.toml": b"[maintenance]\nautomatic = false\n",
        root / "state" / "user-data.json": b'{"preserve":"manager state"}\n',
        Path(env["AGENT_WORKTREES_PROJECTS_YAML"]): b"projects:\n  example:\n    retained: true\n",
        Path(env["AGENT_WORKTREES_REPOS_YAML"]): b"repos:\n  example:\n    retained: true\n",
    }


def _prepare(directory):
    if shutil.which("uv") is None:
        raise RuntimeError("Native uninstall prerequisite unavailable: uv; nothing installed")
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    env = _environment(directory)
    root = Path(env["WORKTREE_MANAGER_ROOT"])
    assert not base.is_relative_to(root.resolve())
    payload = directory / "input"
    print("Manager uninstall native: assemble real standalone payload", flush=True)
    si._copy_payload(SOURCE, payload)
    installer = payload / "scripts" / "runtime_install.py"
    external = [base, "-I", "-S", "-B", installer, "--root", root]
    declared = json.loads((payload / "references" / "uninstall-contract.json").read_text(encoding="utf-8"))
    assert declared["identity"] == "worktree-manager.uninstall" and declared["generation"] == 1

    for path, content in _durable(root, env).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    command = [*external, "--payload", payload, "--", "--version"]
    print("Manager uninstall native: actual canonical construction/health/publication", flush=True)
    first = _success(_run(command, env, directory, timeout=240))
    generation = (root / "current-version").read_text(encoding="utf-8").strip()
    slot = root / "versions" / generation
    origin = _installed_origin(slot, env, directory)
    source_files = {
        name: hashlib.sha256((payload / name).read_bytes()).hexdigest()
        for name in origin["files"]
    }
    assert origin["files"] == source_files
    bin_dir = Path(env["USERPROFILE"]) / ".local" / "bin"
    names = ("worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1") if os.name == "nt" else (
        "worktree-manager",
    )
    frontdoors = [bin_dir / name for name in names]
    provider = Path(env["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"]) / "worktree-manager.json"
    assert all(path.is_file() for path in [*frontdoors, provider])
    registration = json.loads(provider.read_text(encoding="utf-8"))
    assert registration["provider_root"] == str(root)
    entry = bin_dir / ("worktree-manager.cmd" if os.name == "nt" else "worktree-manager")
    launch = [env["COMSPEC"], "/d", "/c", entry, "--version"] if os.name == "nt" else [
        "bash", entry, "--version",
    ]
    assert _success(_run(launch, env, directory)).stdout.strip() == first.stdout.strip()
    return {
        "schema": "worktree-manager.native-uninstall-fixture", "version": 1,
        "platform": sys.platform, "base_python": str(base), "root": str(root),
        "source_files": source_files, "origin": origin, "expected_version": first.stdout.strip(),
    }


def _exercise(directory):
    fixture = json.loads((directory / "fixtures.json").read_text(encoding="utf-8"))
    assert fixture["schema"] == "worktree-manager.native-uninstall-fixture"
    env = _environment(directory)
    root = Path(env["WORKTREE_MANAGER_ROOT"])
    assert str(root) == fixture["root"] and fixture["platform"] == sys.platform
    base = Path(fixture["base_python"])
    payload = directory / "input"
    external = [base, "-I", "-S", "-B", payload / "scripts" / "runtime_install.py", "--root", root]
    slot = root / "versions" / (root / "current-version").read_text(encoding="utf-8").strip()
    bin_dir = Path(env["USERPROFILE"]) / ".local" / "bin"
    names = ("worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1") if os.name == "nt" else (
        "worktree-manager",
    )
    frontdoors = [bin_dir / name for name in names]
    provider = Path(env["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"]) / "worktree-manager.json"
    entry = bin_dir / ("worktree-manager.cmd" if os.name == "nt" else "worktree-manager")
    launch = [env["COMSPEC"], "/d", "/c", entry, "--version"] if os.name == "nt" else [
        "bash", entry, "--version",
    ]
    durable = _durable(root, env)
    installed_python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    sealed = _snapshot([slot])
    print("Manager uninstall native: actual installed daemon start/stop", flush=True)
    daemon = json.loads(_success(_run(
        [installed_python, "-I", "-B", "-c", _DAEMON_CYCLE, root], env, directory,
    )).stdout)
    assert daemon["started"] and daemon["stopped"]
    assert _snapshot([slot]) == sealed, "Daemon launch changed the sealed installed runtime"
    print("Manager uninstall native: actual installed configuration list command", flush=True)
    listing = json.loads(_success(_run(
        [installed_python, "-I", "-B", "-c", _LIST_QUERY], env, directory,
    )).stdout)
    assert listing["manager_listed"]
    assert _snapshot([slot]) == sealed, "Configuration list changed the sealed installed runtime"
    roots = [root, bin_dir, provider.parent, directory / "state"]
    before = _snapshot(roots)
    preview = json.loads(_success(_run([*external, "--uninstall", "--json"], env, directory)).stdout)
    assert preview["status"] == "planned", preview
    assert _snapshot(roots) == before, "Preview changed the actual installation"
    refused = _run([
        installed_python, "-I", "-S", "-B", slot / "payload" / "scripts" / "runtime_install.py",
        "--root", root, "--uninstall", "--apply", "--json",
    ], env, directory)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    self_result = json.loads(refused.stdout)
    assert self_result["status"] == "refused" and self_result["removed"] == [], self_result
    assert _snapshot(roots) == before, "Installed-self refusal had effects"

    print("Manager uninstall native: actual external removal", flush=True)
    removed = json.loads(_success(_run(
        [*external, "--uninstall", "--apply", "--json"], env, directory,
    )).stdout)
    assert removed["status"] == "removed" and removed["removed"], removed
    assert not slot.exists() and not list((root / "versions").iterdir())
    assert not (root / "current-version").exists() and not (root / "last-known-good").exists()
    assert not any(path.exists() for path in [*frontdoors, provider])
    assert all(path.read_bytes() == content for path, content in durable.items())
    absent_before = _snapshot(roots)
    absent = json.loads(_success(_run([*external, "--uninstall", "--apply", "--json"], env, directory)).stdout)
    assert absent["status"] == "absent" and absent["removed"] == [], absent
    assert _snapshot(roots) == absent_before

    print("Manager uninstall native: actual canonical reinstall", flush=True)
    _success(_run([*external, "--payload", payload, "--", "--version"], env, directory, timeout=240))
    final = root / "versions" / (root / "current-version").read_text(encoding="utf-8").strip()
    reinstalled = _installed_origin(final, env, directory)
    assert reinstalled["files"] == fixture["source_files"]
    assert all(path.is_file() for path in [*frontdoors, provider])
    assert all(path.read_bytes() == content for path, content in durable.items())
    assert _success(_run(launch, env, directory)).stdout.strip() == fixture["expected_version"]
    assert not (root / "mux-daemon.lock").exists()
    return {
        "schema": "worktree-manager.native-uninstall-evidence", "version": 1,
        "platform": sys.platform, "base_python": str(base), "root": str(root),
        "source_files": fixture["source_files"], "origin": fixture["origin"], "reinstalled": reinstalled,
        "preview": preview["status"], "installed_self": self_result["status"],
        "daemon": daemon,
        "installation_list": listing,
        "removal": removed["status"], "repeat": absent["status"],
        "removed_runtime_and_frontdoors_confirmed": True,
        "durable_bytes_preserved": True,
    }


def test_real_installed_manager_removal_and_reinstall():
    evidence = json.loads(Path(os.environ["MANAGER_RUNTIME_ACCEPTANCE_DATA"]).read_text(encoding="utf-8"))
    assert evidence["schema"] == "worktree-manager.native-uninstall-evidence"
    assert evidence["platform"] == sys.platform
    assert evidence["preview"] == "planned"
    assert evidence["installed_self"] == "refused"
    assert evidence["removal"] == "removed" and evidence["repeat"] == "absent"
    assert evidence["removed_runtime_and_frontdoors_confirmed"]
    assert evidence["durable_bytes_preserved"]
    assert evidence["daemon"]["started"] and evidence["daemon"]["stopped"]
    assert evidence["installation_list"]["manager_listed"]
    assert evidence["origin"]["files"] == evidence["reinstalled"]["files"] == evidence["source_files"]
    assert Path(evidence["reinstalled"]["module"]).is_file()
    assert not Path(evidence["base_python"]).is_relative_to(Path(evidence["root"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", type=Path)
    action.add_argument("--exercise", type=Path)
    args = parser.parse_args()
    destination = args.prepare or args.exercise
    result = _prepare(destination) if args.prepare else _exercise(destination)
    (destination / "fixtures.json").write_text(json.dumps(result), encoding="utf-8")
