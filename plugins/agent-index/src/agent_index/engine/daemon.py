"""Durable, persistent embedding-engine daemon management.

The engine runs as a long-lived, **warm** daemon from a **durable venv**
(``AGENT_INDEX_ENGINE_HOME``, default ``~/.agent-index/engine``) that lives
**outside** the versioned service runtime -- so a routine service version cutover
never rebuilds the model stack or restarts the engine (effort
agent-index-engine-daemon; vision §warm-durable-engine). The light, torch-free
service runs in ``external`` engine mode and only *talks* to this daemon.

This module manages the daemon's lifecycle **cross-platform**: resolve the durable
venv's interpreter, start the engine detached + persistent, probe ``/health``, and
stop it. The installer registers a platform-native task that runs
``agent-index engine run`` (a foreground launch of the durable interpreter);
operators use ``agent-index engine {start,stop,status,run}``. The daemon serves the
stable engine HTTP API, so a service of a different code version still talks to it
fine over ``external`` mode -- that decoupling is the whole point.
"""

from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

from agent_procutil import (
    spawn_daemon,
    windowless_python,
    windowless_python_env,
)

from .generation import current_engine_generation


def engine_home() -> Path:
    """Durable engine root (holds the heavy venv), outside the versioned runtime."""
    _default = "~/.agent-index/engine"  # marketplace-isolation: allow legacy-compatibility
    return Path(
        os.environ.get("AGENT_INDEX_ENGINE_HOME", _default)
    ).expanduser()


def engine_venv_python(home: Path | None = None) -> Path:
    """Resolve a completed engine slot, retaining pre-migration legacy engines."""
    home = home or engine_home()
    helper = home / "versioned_runtime.py"
    if any((home / name).exists() or (home / name).is_symlink() for name in (
        "current-version", "last-known-good",
    )):
        spec = importlib.util.spec_from_file_location("_agent_index_engine_runtime", helper)
        if spec is None or spec.loader is None:
            raise FileNotFoundError("Durable engine runtime resolver is unavailable")
        runtime = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runtime
        spec.loader.exec_module(runtime)
        python = runtime.resolve_python(home)
        if python is None:
            raise FileNotFoundError("No completed durable engine runtime is selectable")
        return python
    root = home / ".venv"
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _pid_file(home: Path | None = None) -> Path:
    return (home or engine_home()) / "engine.pid"


def engine_endpoint() -> tuple[str, int]:
    """(host, port) the engine daemon binds/serves on."""
    host = os.environ.get("AGENT_INDEX_ENGINE_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENT_INDEX_ENGINE_PORT", "8421"))
    return host, port


def is_healthy(host: str, port: int, *, timeout: float = 3.0) -> bool:
    """True if the engine's ``/health`` answers (model load optional)."""
    import httpx

    try:
        resp = httpx.get(f"http://{host}:{port}/health", timeout=timeout)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def health(host: str, port: int, *, timeout: float = 3.0) -> dict:
    """Return the engine health payload, or a normalized unreachable result."""
    import httpx

    try:
        resp = httpx.get(f"http://{host}:{port}/health", timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict):
            return payload
    except (httpx.HTTPError, OSError, ValueError):
        pass
    return {
        "status": "unreachable",
        "generation": None,
        "gpu_deps_installed": False,
        "model_loaded": False,
        "model_name": None,
        "device": None,
        "cuda_available": None,
        "python_executable": None,
        "detail": f"Engine not reachable at http://{host}:{port}",
    }


def _write_pid(pid: int, home: Path | None = None) -> None:
    pf = _pid_file(home)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="ascii")


def _read_pid(home: Path | None = None) -> int | None:
    pf = _pid_file(home)
    try:
        return int(pf.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(  # noqa: S603
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],  # noqa: S607
            capture_output=True,
            text=True,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


#: Uvicorn log level for the engine. Spawned engines default to ``warning`` so
#: their append-only stdio log carries startup failures, not one access line
#: per embedding request; a foreground ``engine run`` keeps ``info``.
ENGINE_LOG_LEVEL_ENV = "AGENT_INDEX_ENGINE_LOG_LEVEL"


def engine_log_path(home: Path | None = None) -> Path:
    """Append-only stdout/stderr log of a spawned persistent engine."""
    return Path(os.path.abspath(home or engine_home())) / "logs" / "engine-stdio.log"


#: Runs ``agent_index_engine.app`` as ``__main__`` with ``uvicorn.run`` taking
#: its level from :data:`ENGINE_LOG_LEVEL_ENV`. The durable engine venv is
#: preserved across service updates, so its installed app may predate that
#: variable and hard-code ``info``; this keeps its access lines out of the
#: append-only log without rebuilding the engine.
_LOG_LEVEL_BOOTSTRAP = (
    "import os, runpy, uvicorn\n"
    "_run = uvicorn.run\n"
    "def _run_at_level(*args, **kwargs):\n"
    f"    kwargs['log_level'] = os.environ.get({ENGINE_LOG_LEVEL_ENV!r}) or kwargs.get('log_level', 'info')\n"
    "    return _run(*args, **kwargs)\n"
    "uvicorn.run = _run_at_level\n"
    "runpy.run_module('agent_index_engine.app', run_name='__main__', alter_sys=True)\n"
)


def _with_log_level_bootstrap(cmd: list[str]) -> list[str]:
    if cmd[1:3] == ["-m", "agent_index_engine.app"]:
        return [cmd[0], "-c", _LOG_LEVEL_BOOTSTRAP, *cmd[3:]]
    return list(cmd)


def _spawn(cmd: list[str], *, python: str, home: Path) -> subprocess.Popen:
    """Launch the persistent engine through ``spawn_daemon``.

    Runs from its durable engine home, never the caller's cwd (a checkout that
    may be deleted, or the plugin payload), breaks away from a kill-on-close
    Job around the caller where allowed (otherwise it starts inside it, as it
    always did), and appends stdout/stderr to :func:`engine_log_path` at the
    :data:`ENGINE_LOG_LEVEL_ENV` level, whatever engine app version is installed.
    """
    root = Path(os.path.abspath(home))
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(windowless_python_env(python))
    env.setdefault(ENGINE_LOG_LEVEL_ENV, "warning")
    return spawn_daemon(
        _with_log_level_bootstrap(cmd),
        cwd=root,
        log_path=engine_log_path(root),
        env=env,
        console_children=True,
        contained_fallback=True,
        display="agent_index_engine.app",
    )


def engine_command(home: Path | None = None) -> list[str]:
    """The argv that launches the engine from the durable venv (host/port bound).

    Uses the venv's ``pythonw.exe`` sibling on Windows: the console-subsystem
    ``python.exe`` launcher re-execs the base interpreter as a child even under
    ``CREATE_NO_WINDOW``, and that child allocates its own visible console (a
    single long-lived server, so there is no recurring-console-descendant need
    for a console interpreter here -- unlike the Docker daemon-with-recurring-
    children case).
    """
    host, port = engine_endpoint()
    py = engine_venv_python(home)
    return [
        windowless_python(str(py)),
        "-m",
        "agent_index_engine.app",
        "--host",
        host,
        "--port",
        str(port),
    ]


def start(home: Path | None = None, *, wait_timeout: float | None = None) -> str:
    """Start the persistent engine daemon from the durable venv.

    Returns a short status string. Idempotent: a no-op when already healthy.
    Raises :class:`FileNotFoundError` if the durable engine venv is missing (the
    engine runtime hasn't been provisioned -- run the installer / provisioning).

    ``wait_timeout`` defaults to a generous 300s: a cold process import of
    torch + the embedding model (before uvicorn even starts listening) has
    been observed to take upwards of two minutes on a CPU-only host, well
    past what a short timeout tuned for a lightweight service would allow --
    this is a one-time cost per process start, not a steady-state latency,
    so a long ceiling here costs nothing once healthy. Overridable via
    ``AGENT_INDEX_ENGINE_START_TIMEOUT`` for a host that needs longer still
    (e.g. a slow disk or first-ever model download).
    """
    if wait_timeout is None:
        wait_timeout = float(os.environ.get("AGENT_INDEX_ENGINE_START_TIMEOUT", "300"))
    home = home or engine_home()
    host, port = engine_endpoint()
    if is_healthy(host, port):
        return f"already running at {host}:{port}"

    py = engine_venv_python(home)
    if not py.exists():
        raise FileNotFoundError(
            f"durable engine venv not found at {py}; provision the engine runtime "
            f"first (installer, or 'agent-index engine install')"
        )

    proc = _spawn(engine_command(home), python=str(py), home=home)
    _write_pid(proc.pid, home)

    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"engine daemon exited early (code {proc.returncode}) at {host}:{port}; "
                f"see {engine_log_path(home)}"
            )
        if is_healthy(host, port):
            return f"started (pid {proc.pid}) at {host}:{port}"
        time.sleep(1.0)
    raise TimeoutError(
        f"engine daemon did not become healthy within {wait_timeout:.0f}s at "
        f"{host}:{port}"
    )


def stop(home: Path | None = None) -> str:
    """Stop the persistent engine daemon (best-effort)."""
    home = home or engine_home()
    pid = _read_pid(home)
    if pid is None or not _pid_alive(pid):
        _pid_file(home).unlink(missing_ok=True)
        return "not running"
    if os.name == "nt":
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
        )
    else:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
    _pid_file(home).unlink(missing_ok=True)
    return f"stopped (pid {pid})"


def status(home: Path | None = None) -> dict:
    """Report the daemon's health, bound endpoint, pid, and durable venv presence."""
    home = home or engine_home()
    host, port = engine_endpoint()
    pid = _read_pid(home)
    observed = health(host, port)
    try:
        python = engine_venv_python(home)
    except FileNotFoundError:
        python = None
    return {
        "healthy": observed.get("status") != "unreachable",
        "host": host,
        "port": port,
        "pid": pid,
        "pid_alive": bool(pid and _pid_alive(pid)),
        "engine_home": str(home),
        "venv_python": str(python) if python is not None else None,
        "provisioned": python is not None and python.exists(),
        "generation": current_engine_generation(),
        "observed_generation": observed.get("generation"),
        "gpu_deps_installed": observed.get("gpu_deps_installed"),
        "model_loaded": observed.get("model_loaded"),
        "cuda_available": observed.get("cuda_available"),
        "python_executable": observed.get("python_executable"),
        "detail": observed.get("detail"),
    }


def run_foreground() -> int:
    """Exec the engine in the FOREGROUND from the durable venv.

    This is what a platform-native daemon task invokes: it replaces the current
    process (or falls back to a blocking child) with the durable-venv engine so
    the OS task supervises the real engine process.
    """
    cmd = engine_command()
    py = Path(cmd[0])
    if not py.exists():
        raise FileNotFoundError(
            f"durable engine venv not found at {py}; provision the engine runtime first"
        )
    if os.name != "nt":
        os.execv(str(py), cmd)  # replace this process; never returns  # noqa: S606
    proc = subprocess.Popen(cmd)  # noqa: S603
    return proc.wait()
