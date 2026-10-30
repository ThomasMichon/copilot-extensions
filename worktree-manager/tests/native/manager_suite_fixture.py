"""Windows hosted-VM fixture for real installed whole-suite updates.

Only acquisition/credential delivery is controlled. The suite planner, final
engine, constructors, service launchers and ScheduledTasks producers are real.
The VM owns the outer registration boundary; local execution is refused before
creating files. The coordinator owns containment budgets and workflow wiring.
Run prepare/exercise for normal, then prepare/exercise for repair on the same VM;
do not leave two cases' global registrations resident concurrently. Preparation
retains V1 for exercise. Exercise attempts settlement; the outer owner must
always invoke --settle after each case, including a timed-out native stage.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import ctypes
from ctypes import wintypes
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import sqlite3
import sys
import threading
import time
import tomllib
from typing import Any
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manager_suite_artifacts import (
    _DELIVERY, _MATERIALIZE, _command, _configuration, _env, _task_inventory_script, _write,
)
from manager_suite_diagnostics import _engine_probe, retain_driver_logs

REPO = Path(__file__).resolve().parents[3]
SCHEMA = "worktree-manager.native-suite-replacement.v1"
_COMMAND_LOG_LOCK = threading.Lock()
# Observer capabilities, NOT the applicable portfolio. The installed planner
# chooses that portfolio. Unknown/new residents refuse acceptance.
MARKERS = {
    "agent-worktrees": "status-monitor.lock", "agent-bridge": "running-version.json",
    "agent-codespaces": "connection-owner.live.json", "agent-dispatch": "running-version.json",
    "agent-index": "running-version.json", "agent-vault": r"run\agent-vault-service.pid",
    "worktree-manager": "mux-daemon.lock",
    "agent-index/engine": "engine.pid",
}
CLIENT_DEFAULTS = {
    "agent-containers", "agent-logger", "agent-machines", "agent-mcp",
    "agent-pull-requests", "agent-ssh", "budget-guidance",
}


def require_venue() -> None:
    """This is a test venue check, never a production installation grant."""
    if not (
        os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_RUN_ID") and os.environ.get("RUNNER_TEMP")
    ):
        raise RuntimeError("Full suite requires a disposable GitHub-hosted Windows VM; no local drill")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _run(
    argv: list[Any], case: Path, *, timeout: float = 240, check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> Any:
    from agent_procutil._caller_owned import run

    # Same native owner as run_installation_owned, with explicit cwd/env so
    # concurrent read-only observation cannot race process-global HOME changes.
    result = run(
        list(map(str, argv)), timeout=timeout, input_text=None, capture=True,
        cwd=case / "project", env={**_env(case), **(extra_env or {})},
        allow_daemon_breakaway=True,
        max_stdout_bytes=4 * 1024 * 1024, max_stderr_bytes=4 * 1024 * 1024,
    )
    with _COMMAND_LOG_LOCK, (case / "commands.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "argv": list(map(str, argv)), "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        }) + "\n")
    if result.returncode:
        retain_driver_logs(case, "\n".join((result.stdout, result.stderr)))
        if check:
            raise AssertionError(f"{argv}: {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def _git(case: Path, root: Path, *args: str) -> str:
    return _run([
        shutil.which("git"), "-c", "core.longpaths=true", "-c", "core.autocrlf=false",
        "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=" + str(case / "empty-hooks"), "-C", root, *args,
    ], case).stdout.strip()


def _commit(case: Path, root: Path) -> str:
    _git(case, root, "add", ".")
    _git(case, root, "-c", "user.name=Suite Fixture", "-c",
         "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "Controlled native suite artifact")
    return _git(case, root, "rev-parse", "HEAD")


def _python(root: Path) -> Path:
    generation = (root / "current-version").read_text(encoding="utf-8").strip()
    return root / "versions" / generation / "Scripts" / "python.exe"


def _probe(case: Path, kind: str) -> dict[str, Any]:
    root = case / "h" / (".worktree-manager" if kind == "manager" else ".agent-worktrees")
    result = _run([_python(root), "-I", "-X", "utf8", "-B", __file__, "--probe", kind], case, timeout=30)
    return json.loads(result.stdout)


def _snapshot(path: Path) -> dict[str, Any]:
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError(f"Sealed generation missing or indirect: {path}")
    return {str(item.relative_to(path)): [hashlib.sha256(item.read_bytes()).hexdigest(),
            item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_dev, item.stat().st_ino]
            for item in sorted(path.rglob("*")) if item.is_file() and not item.is_symlink()}


def _observe(case: Path, declared: dict[str, Any]) -> dict[str, Any]:
    observed = _probe(case, "engine")
    if set(observed["portfolio"]) != set(declared):
        raise RuntimeError(f"Canonical planner portfolio differs from declarations: {observed}")
    for name, step in observed["portfolio"].items():
        if not step["eligible"] or step["status"] not in ("", "SKIPPED (current)"):
            raise RuntimeError(f"Applicable prerequisite unsatisfied: {name}: {step}")
        if not Path(step["root"]).is_relative_to(case / "h"):
            raise RuntimeError(f"Planner selected a non-default-home installation: {step}")
    roots = {name: Path(row["root"]) for name, row in observed["portfolio"].items()}
    roots["worktree-manager"] = case / "h" / ".worktree-manager"
    observed["roots"] = {name: str(root) for name, root in roots.items()}
    observed["versions"] = {
        name: (root / "current-version").read_text(encoding="utf-8").strip()
        for name, root in roots.items()
    }
    observed["slots"] = {
        name: str(root / "versions" / observed["versions"][name]) for name, root in roots.items()
    }
    observed["sealed"] = {name: _snapshot(Path(slot)) for name, slot in observed["slots"].items()}
    observed["origins"] = {}
    observed["commands"] = {}
    for name, root in roots.items():
        payload = case / "published" / "worktree-manager" if name == "worktree-manager" else (
            case / "published" / "plugins" / name
        )
        metadata = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        result = _run([
            _python(root), "-I", "-X", "utf8", "-B", __file__, "--probe", "identity",
            "--component", name, "--distribution", metadata["name"],
        ], case, timeout=30)
        origin = json.loads(result.stdout)
        if not Path(origin["module"]).resolve().is_relative_to(Path(origin["prefix"]).resolve()):
            raise RuntimeError(f"{name}: imported module is outside its installed prefix: {origin}")
        observed["origins"][name] = origin
        if name == "worktree-manager":
            commands = [{"command": name, "module": "worktree_manager", "variants": ["cmd", "ps1", ""]}]
        else:
            invocation = _read(payload / "payload-invocation.json")
            commands = invocation.get("commands") or [invocation]
            commands = [{"command": row["command"], "module": row["module"],
                         "variants": ["cmd", "ps1"]} for row in commands]
        observed["commands"][name] = commands
    engine = roots["agent-index"] / "engine"
    selected = (engine / "current-version").read_text(encoding="utf-8").strip()
    observed["index_engine"] = {
        "root": str(engine), "selected": selected,
        "slot": str(engine / "versions" / selected),
        "sealed": _snapshot(engine / "versions" / selected),
        "policy": "native Install-Engine preserves a completed independent engine",
    }
    observed["resident_roots"] = {name: str(roots[name]) for name in MARKERS if name in roots}
    observed["resident_roots"]["agent-index/engine"] = str(engine)
    observed["resident_targets"] = {
        name: observed["origins"][name]["version"] for name in observed["resident_roots"]
        if name != "agent-index/engine"
    }
    server_project = case / "published" / "plugins" / "agent-index" / "server" / "pyproject.toml"
    observed["resident_targets"]["agent-index/engine"] = tomllib.loads(
        server_project.read_text(encoding="utf-8"),
    )["project"]["version"]
    return observed


def _tasks(case: Path) -> list[dict[str, Any]]:
    script = _task_inventory_script(case / "h")
    result = _run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script], case, timeout=30)
    (case / "task-inventory.json").write_text(result.stdout, encoding="utf-8")
    rows = json.loads(result.stdout)
    home = str(case / "h").casefold()
    owned = re.compile(re.escape(home) + r"""(?=[\\/"'<]|$)""")
    return [row for row in rows if owned.search(row["xml"].casefold())]


def _record(root: Path, name: str) -> tuple[dict[str, Any], int]:
    if name in {"worktree-manager", "agent-worktrees", "agent-bridge", "agent-dispatch", "agent-index"}:
        from zdd.routing import read_active_endpoint

        route = read_active_endpoint(root / {
            "worktree-manager": "mux-daemon-routing", "agent-worktrees": "status-monitor-routing",
        }.get(name, ""))
        if route is None:
            raise FileNotFoundError(f"{name}: published resident route missing at {root}")
        return {"pid": route.pid}, 0
    marker = MARKERS.get(name)
    if marker is None:
        raise RuntimeError(f"{name}: no resident marker recognizer; do not reduce the portfolio")
    path = root / marker
    if not path.is_file():
        raise FileNotFoundError(
            f"{name}: missing real resident marker {path}; canonical lifecycle/observer prerequisite unmet",
        )
    value = {"pid": int(path.read_text().strip())} if name in {
        "agent-vault", "agent-index/engine",
    } else _read(path)
    if not isinstance(value.get("pid"), int) or value["pid"] <= 0:
        raise RuntimeError(f"{name}: unsupported resident PID evidence at {path}")
    return value, path.stat().st_mtime_ns


def _resident_probe(name: str, root: Path, expected_pid: int) -> dict[str, Any]:
    """Run inside the installed interpreter; never substitute selected version."""
    record, _ = _record(root, name)
    if record["pid"] != expected_pid:
        raise RuntimeError("Resident changed before health observation")
    if name == "worktree-manager":
        from worktree_manager import mux_daemon_cutover as control
        route = control.routed_endpoint(root)
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Manager route does not name the observed PID")
        status = control.ControlClient(route.base_url, root=root, timeout=1).health()
        ready = status.get("status") == "ready"
        generation = status.get("runtime_generation")
    elif name == "agent-worktrees":
        from agent_worktrees import status_monitor_cutover as control
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(control.routing_dir(root))
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Worktrees route does not name the observed PID")
        status = control.ControlClient(route.base_url, config_dir=root, timeout=1).health()
        ready = status.get("status") == "ready"
        generation = status.get("runtime_generation")
    elif name == "agent-bridge":
        from agent_bridge.client import BridgeClient
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(root)
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Bridge route does not name the observed PID")
        client = BridgeClient.from_config()
        client._timeout, client._connect_grace = 1, 0
        status = client.health()
        ready = status.get("ready") is True and not status.get("draining")
        if status.get("slot", {}).get("pid") != expected_pid:
            raise RuntimeError("Bridge health PID mismatch")
        generation = record.get("generation_id")
    elif name == "agent-dispatch":
        from agent_dispatch import config
        from agent_dispatch.client import DispatchClient
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(config.routing_dir())
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Dispatch route does not name the observed PID")
        with DispatchClient(route.base_url, token=config.client_token(), timeout=1) as client:
            status = client.health()
        ready = status.get("status") == "ok" and not status.get("draining")
        if status.get("slot", {}).get("pid") != expected_pid:
            raise RuntimeError("Dispatch health PID mismatch")
        generation = str(route.generation)
    elif name == "agent-index":
        from agent_index import config
        from agent_index.__main__ import _owned_service_client
        from zdd.routing import read_active_endpoint
        route = read_active_endpoint(config.routing_dir())
        if route is None or route.pid != expected_pid:
            raise RuntimeError("Index route does not name the observed PID")
        status, _client = _owned_service_client(route.base_url, timeout=1)
        ready = status.get("status") == "ok"
        if status.get("pid") != expected_pid:
            raise RuntimeError("Index health PID mismatch")
        generation = status.get("runtimeVersion", status.get("version"))
    elif name == "agent-vault":
        from agent_vault.service import send_command
        status = send_command({"action": "ping"})
        ready = isinstance(status, dict) and status.get("ok") is True
        if not ready or status.get("pid") != expected_pid:
            raise RuntimeError("Vault ping did not authenticate the observed service PID")
        generation = status.get("runtime_generation")
    elif name == "agent-index/engine":
        from agent_index.engine import daemon
        status = daemon.health(*daemon.engine_endpoint(), timeout=1)
        if status.get("pid") != expected_pid:
            raise RuntimeError(
                "agent-index engine health must attest its exact PID; owner: "
                "plugins/agent-index/server/src/agent_index_engine/app.py::HealthResponse/health",
            )
        ready = status.get("status") == "ok"
        generation = status.get("generation")
    elif name == "agent-codespaces":
        from agent_codespaces.connection_owner import read_liveness
        beacon = read_liveness()
        if beacon is None or beacon.pid != expected_pid:
            raise RuntimeError("Codespaces beacon PID mismatch")
        ready = beacon.is_fresh()
        status = {"version": beacon.version}
        generation = record.get("process_identity")
    else:
        raise RuntimeError(f"{name}: resident health adapter missing")
    if not status.get("version"):
        raise RuntimeError(
            f"{name}: running producer lacks attributable version; "
            "VaultService ping / connection_owner._write_liveness must publish the running build",
        )
    if not ready:
        raise RuntimeError(f"{name}: actual resident health is not ready")
    return {
        "pid": expected_pid, "version": status["version"],
        "runtime_generation": generation, "ready": True, "ready_observed_ns": time.time_ns(),
        "dependencies": {key: status[key] for key in (
            "gpu_deps_installed", "cuda_available", "device", "model_loaded", "cli",
        ) if key in status},
    }


def _health(case: Path, before: dict[str, Any], name: str, pid: int) -> dict[str, Any]:
    root = Path(before["resident_roots"][name])
    interpreter_root = Path(before["roots"]["agent-index"]) if name == "agent-index/engine" else root
    result = _run([
        _python(interpreter_root), "-I", "-X", "utf8", "-B", __file__, "--probe", "resident",
        "--component", name, "--root", root, "--pid", pid,
    ], case, timeout=10)
    health = json.loads(result.stdout)
    with closing_process(pid) as process:
        created, _exited, code = process.times()
        if code != 259:
            raise RuntimeError(f"{name}: health replied for an exited process")
        health.update(created_ns=created, exit_code=code)
    return health


class closing_process:
    def __init__(self, pid: int) -> None:
        self.process = Process(pid)

    def __enter__(self) -> Process:
        return self.process

    def __exit__(self, *_args: Any) -> None:
        self.process.close()


class Process:
    """Retain a handle to one marker-named process; never enumerate or kill."""

    def __init__(self, pid: int) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.pid = pid

    def times(self) -> tuple[int, int, int]:
        values = [wintypes.FILETIME() for _ in range(4)]
        code = wintypes.DWORD()
        if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(v) for v in values)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())

        def ns(value: Any) -> int:
            ticks = (value.dwHighDateTime << 32) | value.dwLowDateTime
            return (ticks - 116444736000000000) * 100 if ticks else 0

        return ns(values[0]), ns(values[1]), code.value

    def close(self) -> None:
        self.kernel.CloseHandle(self.handle)


def _durable(paths: list[Path]) -> dict[str, Any]:
    return {str(path): [hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_dev, path.stat().st_ino] for path in paths}


def _exclusive(case: Path, root: Path) -> bool:
    helper = case / "published" / "libs" / "versioned-runtime" / "versioned_runtime_leases.py"
    lease = runpy.run_path(str(helper))["_publication_lease"]
    with lease(root, timeout=0):
        result = _run([
            sys.executable, "-I", "-X", "utf8", "-B", __file__, "--probe", "lease",
            "--helper", helper, "--root", root,
        ], case, timeout=30)
    return json.loads(result.stdout)["contended"]


def _frontdoors(case: Path, after: dict[str, Any]) -> dict[str, Any]:
    result = {}
    traces = case / "frontdoor-traces"
    traces.mkdir(exist_ok=True)
    for name, commands in after["commands"].items():
        rows = {}
        for declaration in commands:
            for extension in declaration["variants"]:
                command = declaration["command"]
                basename = command + ("." + extension if extension else "")
                path = case / "h" / ".local" / "bin" / basename
                if not path.is_file():
                    raise RuntimeError(f"{name}: declared Windows frontdoor was not produced: {path}")
                trace = traces / (basename + ".jsonl")
                argv = ["--version"] if name == "worktree-manager" else ["--help"]
                invoked = _run(
                    _command(case, command, *argv, extension=extension), case, timeout=30,
                    extra_env={"COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH": str(trace)},
                )
                if name == "worktree-manager":
                    version = after["origins"][name]["version"].replace("-dev", ".dev")
                    current = version in invoked.stdout.replace("-dev", ".dev")
                    proof = {"kind": "installed Manager version command", "version": version}
                else:
                    emitted = [_read_line for _read_line in (
                        json.loads(line) for line in trace.read_text(encoding="utf-8-sig").splitlines()
                    ) if _read_line.get("phase") == "resolver-slot-result"]
                    current = any(row.get("version") == after["versions"][name]
                                  and row.get("result") == "resolved" for row in emitted)
                    proof = {"kind": "actual native resolver boot trace", "records": emitted}
                if not current:
                    raise RuntimeError(f"{basename}: no actual V2 routing proof; owner must supply a read-only probe")
                rows[basename] = {
                    "returncode": invoked.returncode, "routes_current": current,
                    "module": declaration["module"], "proof": proof,
                }
        result[name] = rows
    return result


def _registration_routes(case: Path, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for task in _tasks(case):
        xml = ElementTree.fromstring(task["xml"])
        arguments = " ".join(node.text or "" for node in xml.iter()
                             if node.tag.rsplit("}", 1)[-1] in {"Arguments", "WorkingDirectory"})
        match = re.search(r"""(?i)-File\s+["']?([^"']+?\.ps1)""", arguments)
        if match is None:
            raise RuntimeError(f"{task['name']}: scheduled producer has no recognized owned launcher")
        launcher = Path(match.group(1))
        if not launcher.resolve().is_relative_to(case / "h"):
            raise RuntimeError(f"{task['name']}: launcher escapes the case HOME")
        body = launcher.read_text(encoding="utf-8-sig")
        for old_slot in [*before["slots"].values(), before["index_engine"]["slot"]]:
            if old_slot.casefold() in (body + arguments).casefold():
                raise RuntimeError(f"{task['name']}: scheduled launcher still invokes a V1 prefix")
        matches = [name for name, root in after["roots"].items()
                   if root.casefold() in (body + arguments).casefold()]
        engine = after["index_engine"]
        is_engine = engine["root"].casefold() in (body + arguments).casefold()
        if is_engine:
            if "resolve-python" not in body or "versioned_runtime.py" not in body:
                raise RuntimeError(f"{task['name']}: unknown independent-engine task resolver")
            native = _run([
                _python(Path(engine["root"])), "-I", "-X", "utf8", "-B",
                Path(engine["root"]) / "versioned_runtime.py",
                "--root", engine["root"], "resolve-python",
            ], case, timeout=30)
            resolved = Path(native.stdout.strip()).resolve()
            prefix = Path(engine["slot"]).resolve()
            if not resolved.is_relative_to(prefix):
                raise RuntimeError(f"{task['name']}: independent-engine task did not resolve its V2 slot")
            rows.append({"name": task["name"], "xml": task["xml"], "launcher": str(launcher),
                         "routes_current": True, "resolver_proofs": [
                             {"component": "agent-index/engine", "python": str(resolved), "prefix": str(prefix)},
                         ]})
            continue
        if not matches or "resolve-runtime.ps1" not in body:
            raise RuntimeError(f"{task['name']}: no recognized canonical task runtime resolver")
        # A scheduler definition pointing at a stable launcher is not proof.
        # Execute its actual canonical resolver read-only and compare V2 prefix.
        proofs = []
        for name in matches:
            root = Path(after["roots"][name])
            resolver = root / "bin" / "resolve-runtime.ps1"
            quoted_root, quoted_resolver = (str(value).replace("'", "''") for value in (root, resolver))
            code = (
                "$ErrorActionPreference='Stop';"
                f"$env:AGENT_RT_ROOT='{quoted_root}'; . '{quoted_resolver}' -TraceMode read-only;"
                "if(-not $AgentRtPy){throw 'Task runtime unresolved'}; Write-Output $AgentRtPy"
            )
            native = _run([shutil.which("pwsh"), "-NoProfile", "-Command", code], case, timeout=30)
            resolved = Path(native.stdout.strip()).resolve()
            prefix = Path(after["origins"][name]["prefix"]).resolve()
            if not resolved.is_relative_to(prefix):
                raise RuntimeError(f"{task['name']}: actual task resolver did not select V2: {resolved}")
            proofs.append({"component": name, "python": str(resolved), "prefix": str(prefix)})
        rows.append({"name": task["name"], "xml": task["xml"], "launcher": str(launcher),
                     "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                     "routes_current": True, "resolver_proofs": proofs})
    return rows


def _watch_resident(name: str, root: Path, old_pid: int, control: Path) -> dict[str, Any]:
    # One prewarmed native observer per resident avoids mistaking interpreter
    # startup latency or a marker's last-write time for a readiness event.
    with closing_process(old_pid) as incumbent:
        original = _resident_probe(name, root, old_pid)
        old_created, _old_exit, code = incumbent.times()
        if code != 259:
            raise RuntimeError(f"{name}: incumbent exited before observation was armed")
        _write(control / "ready.json", original)
        deadline = time.monotonic() + 840
        last_error = "No distinct successor was published"
        while time.monotonic() < deadline and not (control / "stop").exists():
            try:
                marker, _ = _record(root, name)
                if marker["pid"] != old_pid:
                    health = _resident_probe(name, root, marker["pid"])
                    with closing_process(marker["pid"]) as successor:
                        created, _exit, new_code = successor.times()
                    _created, old_exit, old_code = incumbent.times()
                    return {"observation": {
                        "old_pid": old_pid, "old_created_ns": old_created,
                        "old_version": original["version"],
                        "old_runtime_generation": original["runtime_generation"],
                        "old_dependencies": original["dependencies"],
                        "new_pid": marker["pid"], "new_created_ns": created,
                        "new_version": health["version"],
                        "new_runtime_generation": health["runtime_generation"],
                        "new_dependencies": health["dependencies"],
                        "new_exit_code": new_code, "new_exit_code_at_ready": new_code,
                        "new_ready_observed_ns": health["ready_observed_ns"],
                        "old_exit_ns_at_ready": old_exit, "old_exit_code_at_ready": old_code,
                    }}
            except Exception as error:
                last_error = f"{type(error).__name__}: {error}"
            time.sleep(0.02)
        return {"error": last_error}


def _cleanup(case: Path) -> list[str]:
    errors = []
    try:
        tasks = _tasks(case)
    except Exception as error:
        return [f"Owned registration inventory: {error}"]
    # Prevent an owned restart-on-failure task from racing normal API shutdown.
    for row in tasks:
        try:
            name, path = (value.replace("'", "''") for value in (row["name"], row["path"]))
            _run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
                  f"$ErrorActionPreference='Stop'; Disable-ScheduledTask -TaskName '{name}' "
                  f"-TaskPath '{path}' | Out-Null"], case, timeout=30)
        except Exception as error:
            errors.append(f"Owned registration disable {row['name']}: {error}")
    for name in sorted(set(MARKERS) - {
        "agent-worktrees", "worktree-manager", "agent-codespaces", "agent-index/engine",
    }):
        payload = case / "h" / ".copilot" / "installed-plugins" / "copilot-extensions" / name
        installer = payload / "scripts" / "install.ps1"
        try:
            root = case / "h" / ("." + name)
            try:
                record, _ = _record(root, name)
                with closing_process(record["pid"]) as process:
                    alive = process.times()[2] == 259
            except (FileNotFoundError, ProcessLookupError):
                alive = False
            except OSError as error:
                if getattr(error, "winerror", None) not in {87, 1168}:
                    raise
                alive = False
            if alive and installer.is_file():
                _run([shutil.which("pwsh"), "-NoProfile", "-File", installer, "stop"], case, timeout=30)
        except Exception as error:
            errors.append(f"{name} normal producer stop: {error}")
    try:
        manager = case / "h" / ".worktree-manager"
        try:
            marker, _ = _record(manager, "worktree-manager")
            with closing_process(marker["pid"]) as process:
                alive = process.times()[2] == 259
        except (FileNotFoundError, ProcessLookupError):
            alive = False
        except OSError as error:
            if getattr(error, "winerror", None) not in {87, 1168}:
                raise
            alive = False
        if alive:
            _run([_python(manager), "-I", "-X", "utf8", "-B", "-c",
                  "from worktree_manager import mux_daemon_cutover as c;"
                  "e=c.routed_endpoint();"
                  "print(c.ControlClient(e.base_url).shutdown() if e else 'absent')"], case, timeout=30)
    except Exception as error:
        errors.append(f"Manager normal shutdown: {error}")
    try:
        root = case / "h" / ".agent-worktrees"
        marker, _ = _record(root, "agent-worktrees")
        with closing_process(marker["pid"]) as process:
            alive = process.times()[2] == 259
        if alive:
            _run([_python(root), "-I", "-X", "utf8", "-B", "-c",
                  "from agent_worktrees import status_monitor_cutover as c;"
                  "from zdd.routing import read_active_endpoint;"
                  "e=read_active_endpoint(c.routing_dir());"
                  "print(c.ControlClient(e.base_url).shutdown() if e else 'absent')"], case, timeout=30)
    except (FileNotFoundError, ProcessLookupError):
        pass
    except OSError as error:
        if getattr(error, "winerror", None) not in {87, 1168}:
            errors.append(f"Worktrees resident identity: {error}")
    except Exception as error:
        errors.append(f"Worktrees normal shutdown: {error}")
    try:
        engine = case / "h" / ".agent-index" / "engine"
        if (engine / "engine.pid").is_file():
            record, _ = _record(engine, "agent-index/engine")
            with closing_process(record["pid"]) as process:
                alive = process.times()[2] == 259
            if alive:
                _run(_command(case, "agent-index", "engine", "stop"), case, timeout=30)
    except (FileNotFoundError, ProcessLookupError):
        pass
    except OSError as error:
        if getattr(error, "winerror", None) not in {87, 1168}:
            errors.append(f"Independent Index engine identity: {error}")
    except Exception as error:
        errors.append(f"Independent Index engine normal shutdown: {error}")
    # Codespaces install.ps1 has no stop action. Its exact scheduler roots
    # below are the available Windows lifecycle owner, not a fictional CLI.
    for row in tasks:
        try:
            # Only exact registrations whose actual exported XML names this HOME.
            name, path = (value.replace("'", "''") for value in (row["name"], row["path"]))
            _run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
                  f"$ErrorActionPreference='Stop'; Stop-ScheduledTask -TaskName '{name}' -TaskPath '{path}';"
                  f"Unregister-ScheduledTask -TaskName '{name}' -TaskPath '{path}' -Confirm:$false"], case, timeout=30)
        except Exception as error:
            errors.append(f"Owned registration settlement {row['name']}: {error}")
    known = list(_read(case / "setup.json").get("before", {}).get("resident_observations", {}).values())
    report_path = case / "result.json"
    if report_path.is_file():
        for row in _read(report_path).get("residents", {}).values():
            known.extend([{"pid": row["old_pid"], "created_ns": row["old_created_ns"]},
                          {"pid": row["new_pid"], "created_ns": row["new_created_ns"]}])
    grace = time.monotonic() + 5
    for row in {(row["pid"], row["created_ns"]): row for row in known}.values():
        try:
            with closing_process(row["pid"]) as process:
                while process.times()[2] == 259 and time.monotonic() < grace:
                    time.sleep(0.05)
                created, _exited, code = process.times()
                if created == row["created_ns"] and code == 259:
                    errors.append(f"Owned resident PID {row['pid']} remains live; disposable VM retains custody")
        except OSError as error:
            if getattr(error, "winerror", None) not in {87, 1168}:
                errors.append(f"Owned resident identity unconfirmed: {error}")
    return errors


def _case(directory: Path, mode: str) -> tuple[Path, dict[str, Any]]:
    require_venue()
    directory = directory.resolve()
    if mode not in {"normal", "repair"} or directory == REPO or not directory.is_relative_to(REPO):
        raise ValueError("Native stage must name a prepared mode below this checkout")
    case = directory / mode
    setup = _read(case / "setup.json")
    if (setup.get("schema") != SCHEMA or setup.get("mode") != mode
            or setup.get("case_root") != str(case) or case.is_symlink()):
        raise ValueError("Prepared fixture schema/mode/root ownership mismatch")
    return case, setup


def settle(directory: Path, mode: str) -> None:
    """Idempotently settle only this prepared case after success or timeout."""
    case, _setup = _case(directory, mode)
    errors = []
    try:
        errors.extend(_cleanup(case))
    except Exception as error:
        errors.append(f"Settlement failed: {type(error).__name__}: {error}")
    try:
        remaining = _tasks(case)
    except Exception as error:
        errors.append(f"Settlement inventory failed: {error}")
        remaining = []
    if remaining:
        errors.append("Owned scheduled registrations remain: " + repr([row["name"] for row in remaining]))
    _write(case / "settlement.json", {"schema": SCHEMA, "mode": mode, "errors": errors,
                                    "remaining_tasks": [row["name"] for row in remaining]})
    if errors:
        raise AssertionError(json.dumps(errors, indent=2))


def prepare(directory: Path, mode: str) -> None:
    """Publish V1/V2 fixture artifacts and start V1 through real producers."""
    require_venue()
    directory = directory.resolve()
    if mode not in {"normal", "repair"} or directory == REPO or not directory.is_relative_to(REPO):
        raise ValueError("Native fixture storage must be an explicit directory below this checkout")
    case = directory / mode
    case.mkdir(parents=True, exist_ok=False)
    (case / "project").mkdir()
    env = _env(case)
    _write(case / "setup.json", {"schema": SCHEMA, "mode": mode, "case_root": str(case),
                                 "ready": False, "declared": {}})
    missing = [name for name in ("uv", "git", "pwsh") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"Native suite prerequisites unavailable: {missing}")
    base = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    # checkout-index is deliberately an INDEX snapshot, not a dirty-worktree
    # snapshot. Refuse silent omission of the coordinator's current source.
    dirty = _git(case, REPO, "diff", "--name-only")
    if dirty:
        raise RuntimeError(f"Native artifact input has unstaged tracked changes; stage exact inputs first:\n{dirty}")
    index = _git(case, REPO, "ls-files", "--stage")
    for required in ("manager_suite_fixture.py", "test_manager_suite_replacement.py"):
        if "worktree-manager/tests/native/" + required not in index:
            raise RuntimeError(f"Native driver input is absent from the source index: {required}")
    for name, count in (("published", 1), ("candidate", 2)):
        target = case / name
        target.mkdir()
        _git(case, REPO, "checkout-index", "--all", "--prefix", str(target) + os.sep)
        _run([base, "-I", "-X", "utf8", "-B", "-c", _MATERIALIZE, target, str(count)], case)
        _git(case, target, "init", "-q", "-b", "main", "--template=")
        _commit(case, target)
    if index != _git(case, REPO, "ls-files", "--stage") or _git(case, REPO, "diff", "--name-only"):
        raise RuntimeError("Source index/worktree changed while preparing immutable artifacts")
    declared = {}
    for path in (case / "published" / "plugins").glob("*/plugin.json"):
        manifest = _read(path)
        if manifest.get("runtimeScope", "none") != "none":
            declared[path.parent.name] = {"scope": manifest["runtimeScope"], "version": manifest["version"]}
    setup = {
        "schema": SCHEMA, "mode": mode, "case_root": str(case), "base": base,
        "declared": declared, "ready": False, "input_policy": "clean worktree, staged-index snapshot",
        "source_index_sha256": hashlib.sha256(index.encode()).hexdigest(),
        "source_head": _git(case, REPO, "rev-parse", "HEAD"),
        "profiles": {"agent-index": "explicit native host, full server/independent engine dependencies",
                     "others": "native defaults; no NoService/NoSupervisor/dependency suppression"},
    }
    _write(case / "setup.json", setup)
    tools = case / "tools"
    tools.mkdir()
    (tools / "delivery.py").write_text(_DELIVERY, encoding="utf-8")
    for name in ("copilot", "gh"):
        (tools / (name + ".cmd")).write_text(
            f'@echo off\n"{base}" -I -X utf8 -B "{tools / "delivery.py"}" {name} %*\nexit /b %errorlevel%\n',
            encoding="utf-8",
        )
    _configuration(case, declared)
    _git(case, case / "project", "init", "-q", "-b", "main", "--template=")
    _commit(case, case / "project")
    _git(case, case / "project", "remote", "add", "origin", str(case / "project"))
    _git(case, case / "project", "fetch", "-q", "origin", "main")
    _git(case, case / "project", "branch", "--set-upstream-to=origin/main", "main")
    try:
        _run([sys.executable, "-I", "-X", "utf8", "-B", "-c",
              "from pathlib import Path;import sys;"
              "from worktree_manager.self_install import _copy_payload;"
              "_copy_payload(Path(sys.argv[1]),Path(sys.argv[2]))",
              case / "published" / "worktree-manager", case / "manager-payload"], case)
        _run([base, "-I", "-X", "utf8", "-B", case / "manager-payload" / "scripts" / "runtime_install.py",
              "--root", Path(env["HOME"]) / ".worktree-manager",
              "--payload", case / "manager-payload", "--", "--version"], case)
        payload = Path(env["HOME"]) / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-worktrees"
        _run([shutil.which("pwsh"), "-NoProfile", "-File", payload / "scripts" / "install.ps1", "install"], case)
        _run(_command(case, "worktree-manager", "update", "--project", "example"), case, timeout=840)
        _run([shutil.which("pwsh"), "-NoProfile", "-File", payload.with_name("agent-index") / "scripts" / "install.ps1",
              "engine"], case, timeout=600)
        _run(_command(case, "worktree-manager", "mux-daemon", "ensure"), case, timeout=30)
        owner_tasks = [row for row in _tasks(case) if row["name"] == "agent-codespaces-owner"]
        if len(owner_tasks) != 1:
            raise RuntimeError("Baseline Codespaces owner requires its exact owned native task")
        task_path = owner_tasks[0]["path"].replace("'", "''")
        _run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
              "$ErrorActionPreference='Stop'; Start-ScheduledTask -TaskName 'agent-codespaces-owner' "
              f"-TaskPath '{task_path}'"], case, timeout=30)
        _run([_python(case / "h" / ".agent-codespaces"), "-I", "-X", "utf8", "-B", "-c",
              "import time\nfrom agent_codespaces.connection_owner import is_owner_live\n"
              "deadline=time.monotonic()+30\n"
              "while not is_owner_live() and time.monotonic()<deadline: time.sleep(.2)\n"
              "assert is_owner_live(), 'Scheduled Codespaces owner did not become ready'"], case, timeout=60)
        before = _observe(case, declared)
        setup["before"] = before
        setup["required_residents"] = sorted(before["resident_roots"])
        _write(case / "setup.json", setup)
        unknown = set(before["roots"]) - set(MARKERS) - CLIENT_DEFAULTS
        if unknown:
            raise RuntimeError(f"Unsupported resident classification, owner must supply recognizer: {sorted(unknown)}")
        before["resident_observations"] = {}
        for name in setup["required_residents"]:
            marker, _ = _record(Path(before["resident_roots"][name]), name)
            health = _health(case, before, name, marker["pid"])
            if health["version"].replace("-dev", ".dev") != before["resident_targets"][name].replace("-dev", ".dev"):
                raise RuntimeError(f"{name}: actual V1 resident version differs from installed V1")
            before["resident_observations"][name] = health
            _write(case / "setup.json", setup)
        setup["tasks"] = _tasks(case)
        setup["ready"] = True
        _write(case / "setup.json", setup)
    except BaseException as error:
        _write(case / "preparation-error.json", {
            "schema": SCHEMA, "mode": mode,
            "errors": [f"{type(error).__name__}: {error}"], "cleanup_errors": _cleanup(case),
        })
        raise


def exercise(directory: Path, mode: str) -> None:
    """Run the actual installed ordinary/repair command; settle only afterward."""
    case, setup = _case(directory, mode)
    if not setup["ready"]:
        raise ValueError("Fixture mode/schema/readiness does not match a prepared generation")
    report: dict[str, Any] = {
        "schema": SCHEMA, "mode": mode, "venue": "github-hosted-windows",
        "declared": setup["declared"], "before": setup["before"],
        "required_residents": setup["required_residents"], "errors": [], "residents": {},
        "source": str(case / "published"), "cleanup_errors": [],
    }
    before = setup["before"]
    roots = {name: Path(value) for name, value in before["roots"].items()}
    witness = roots["agent-worktrees"] / "continuity.sqlite"
    durable = [roots["worktree-manager"] / "config.toml", witness,
               case / "h" / ".example" / "config.yaml"]
    try:
        with closing(sqlite3.connect(witness)) as connection, connection:
            connection.executescript(
                "CREATE TABLE witness(value TEXT); INSERT INTO witness VALUES('retained');"
                "PRAGMA user_version=7;",
            )
        report["durable_before"] = _durable(durable)
        with ExitStack() as owned:
            old = {}
            for name in report["required_residents"]:
                record, _ = _record(Path(before["resident_roots"][name]), name)
                process = Process(record["pid"])
                owned.callback(process.close)
                previous = before["resident_observations"][name]
                if process.pid != previous["pid"] or process.times()[0] != previous["created_ns"]:
                    raise RuntimeError(f"{name}: V1 resident was not retained across native stages")
                old[name] = process
            arguments = (["update"] if mode == "normal" else [
                "update", "--repair-engine", str(case / "published"), "--force",
            ]) + ["--project", "example"]
            controls = {}
            with ThreadPoolExecutor(max_workers=len(old)) as observer:
                futures = {}
                for name, process in old.items():
                    control = case / "observers" / name.replace("/", "-")
                    control.mkdir(parents=True)
                    controls[name] = control
                    root = Path(before["resident_roots"][name])
                    interpreter_root = roots["agent-index"] if name == "agent-index/engine" else root
                    futures[name] = observer.submit(_run, [
                        _python(interpreter_root), "-I", "-X", "utf8", "-B", __file__, "--probe", "watch",
                        "--component", name, "--root", root, "--pid", process.pid,
                        "--control", control,
                    ], case, timeout=850)
                try:
                    armed = time.monotonic() + 30
                    while not all((path / "ready.json").is_file() for path in controls.values()):
                        for name, future in futures.items():
                            if future.done():
                                future.result()  # Surface a failed real V1 prerequisite immediately.
                                raise RuntimeError(f"{name}: observer exited before the update began")
                        if time.monotonic() >= armed:
                            raise RuntimeError("Native resident observers did not arm in budget")
                        time.sleep(0.02)
                    shutil.copytree(case / "candidate", case / "published", dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(".git"))
                    _commit(case, case / "published")
                    result = _run(_command(case, "worktree-manager", *arguments), case, timeout=840, check=False)
                    report["update"] = {"arguments": arguments, "returncode": result.returncode,
                                        "stdout": result.stdout, "stderr": result.stderr}
                    after = report["after"] = _observe(case, setup["declared"])
                finally:
                    for control in controls.values():
                        (control / "stop").touch()
                observed = {name: json.loads(future.result().stdout)
                            for name, future in futures.items()}
            report["applicability"] = after["portfolio"]
            report["retained"] = {
                name: _snapshot(Path(slot)) == before["sealed"][name]
                for name, slot in before["slots"].items()
            }
            deadline = time.monotonic() + 30
            for name, process in old.items():
                if "observation" not in observed[name]:
                    raise RuntimeError(f"{name}: successor readiness was not observed: {observed[name]}")
                row = observed[name]["observation"]
                while process.times()[2] == 259 and time.monotonic() < deadline:
                    time.sleep(0.05)
                _created, exited, code = process.times()
                with closing_process(row["new_pid"]) as successor:
                    row["new_exit_code"] = successor.times()[2]
                row.update(old_exit_ns=exited, old_exit_code=code)
                report["residents"][name] = row
            report["commands"] = _frontdoors(case, after)
            report["registrations"] = _registration_routes(case, before, after)
            dependency = before["index_engine"]
            report["index_engine_retained"] = (
                _snapshot(Path(dependency["slot"])) == dependency["sealed"]
            )
            report["exclusive_writers"] = {
                name: _exclusive(case, root) for name, root in roots.items()
            }
            lease = _run([
                _python(roots["worktree-manager"]), "-I", "-X", "utf8", "-B", "-c",
                "import json;from worktree_manager import mux_daemon_process as p;"
                "lease=p.acquire_daemon_lease(p.default_root());"
                "print(json.dumps({'held':lease is None}));"
                "p.release_daemon_lease(lease) if lease is not None else None",
            ], case, timeout=30)
            report["exclusive_work"] = json.loads(lease.stdout)["held"]
        report["durable_after"] = _durable(durable)
        with closing(sqlite3.connect(f"file:{witness.as_posix()}?mode=ro", uri=True)) as connection:
            report["witness"] = {
                "rows": connection.execute("SELECT value FROM witness").fetchall(),
                "schema": connection.execute("PRAGMA user_version").fetchone()[0],
            }
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            report["cleanup_errors"] = _cleanup(case)
        except Exception as error:
            report["cleanup_errors"] = [f"Settlement unconfirmed: {type(error).__name__}: {error}"]
        if "retained" in report:
            for name, slot in before["slots"].items():
                try:
                    report["retained"][name] &= _snapshot(Path(slot)) == before["sealed"][name]
                except Exception as error:
                    report["retained"][name] = False
                    report["cleanup_errors"].append(f"Post-settlement V1 evidence {name}: {error}")
            try:
                report["index_engine_retained"] = report.get("index_engine_retained", True) and (
                    _snapshot(Path(before["index_engine"]["slot"])) == before["index_engine"]["sealed"]
                )
                report["durable_after"] = _durable(durable)
            except Exception as error:
                report["cleanup_errors"].append(f"Post-settlement durable/dependency evidence: {error}")
        _write(case / "result.json", report)
    if report["errors"] or report["cleanup_errors"]:
        raise AssertionError(json.dumps(report["errors"] + report["cleanup_errors"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--probe", choices=("engine", "identity", "lease", "resident", "watch"), required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--component")
    parser.add_argument("--distribution")
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--control", type=Path)
    options = parser.parse_args()
    require_venue()
    if options.probe == "engine":
        print(json.dumps(_engine_probe()))
    elif options.probe == "identity":
        module = importlib.import_module(options.component.replace("-", "_"))
        print(json.dumps({"prefix": sys.prefix, "module": module.__file__,
                          "version": importlib.metadata.version(options.distribution)}))
    elif options.probe == "resident":
        print(json.dumps(_resident_probe(options.component, options.root, options.pid)))
    elif options.probe == "watch":
        print(json.dumps(_watch_resident(options.component, options.root, options.pid, options.control)))
    else:
        lease = runpy.run_path(str(options.helper))["_publication_lease"]
        try:
            with lease(options.root, timeout=0):
                contended = False
        except TimeoutError:
            contended = True
        print(json.dumps({"contended": contended}))
