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
import subprocess
import time
from pathlib import Path

from agent_procutil import windowless_daemon_kwargs, windowless_python

from .generation import current_engine_generation


def engine_home() -> Path:
    """Durable engine root (holds the heavy venv), outside the versioned runtime."""
    return Path(
        os.environ.get("AGENT_INDEX_ENGINE_HOME", "~/.agent-index/engine")
    ).expanduser()


def engine_venv_python(home: Path | None = None) -> Path:
    """Path to the durable engine venv's Python interpreter."""
    root = (home or engine_home()) / ".venv"
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


def _spawn(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": os.environ.copy(),
    }
    kwargs.update(windowless_daemon_kwargs())
    return subprocess.Popen(cmd, **kwargs)  # type: ignore[arg-type]  # noqa: S603


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
        "agent_index.engine.app",
        "--host",
        host,
        "--port",
        str(port),
    ]


def start(home: Path | None = None, *, wait_timeout: float = 90.0) -> str:
    """Start the persistent engine daemon from the durable venv.

    Returns a short status string. Idempotent: a no-op when already healthy.
    Raises :class:`FileNotFoundError` if the durable engine venv is missing (the
    engine runtime hasn't been provisioned -- run the installer / provisioning).
    """
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

    proc = _spawn(engine_command(home))
    _write_pid(proc.pid, home)

    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"engine daemon exited early (code {proc.returncode}) at {host}:{port}"
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
    return {
        "healthy": observed.get("status") != "unreachable",
        "host": host,
        "port": port,
        "pid": pid,
        "pid_alive": bool(pid and _pid_alive(pid)),
        "engine_home": str(home),
        "venv_python": str(engine_venv_python(home)),
        "provisioned": engine_venv_python(home).exists(),
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
