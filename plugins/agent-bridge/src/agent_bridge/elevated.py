"""Elevated sub-daemon launcher (Windows) for Capability 2.

The primary (non-elevated) agent-bridge cannot spawn an *elevated* Copilot
directly. Instead it launches a second agent-bridge instance -- the **elevated
sub-daemon** -- bound to a separate loopback port and running with a full admin
token, then relays elevated agents to it over ACP-over-WebSocket (see
``acp_connect.py`` and ``routes/acp_ws.py``). Because the whole sub-daemon is
elevated, any agent it spawns (e.g. an enlistment-based ``base_repo`` agent) runs
elevated -- no per-agent ``gsudo`` wrapping.

Isolation: the sub-daemon runs with ``AGENT_BRIDGE_CONFIG_DIR`` pointed at
``<primary config dir>/elevated`` so it has its own ``config.yaml`` (distinct
port + db), its own ``auth.yaml`` (token), and its own ``sessions.db``. Local
agents are still auto-discovered from the shared
``~/.agent-worktrees/projects.yaml``.

Elevation: a scheduled task with ``RunLevel=HIGHEST`` runs the daemon with the
admin token. Creating such a task requires elevation, so we register+run it via a
single ``Start-Process -Verb RunAs`` bootstrap (one UAC prompt). Subsequent
``schtasks /run`` calls within the session are prompt-free. This is the
Task-Scheduler mechanism chosen for the effort; the sub-daemon is session-scoped.

Security note (v1): the sub-daemon binds loopback only and is gated by its bearer
token, but that token lives in a user-readable file, so another process running
as the same user could in principle drive the elevated agent. Acceptable on a
single-user dev box; hardening (admin-only token / launch-nonce handshake) is
tracked separately.
"""

# This module intentionally shells out to Windows `schtasks`/`powershell` by name
# (S607) to manage a scheduled task (S603), and polls a fixed loopback HTTP health
# endpoint (S310). These are by-design and trusted here.
# ruff: noqa: S603, S607, S310

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from .config import config_dir, load_config

log = logging.getLogger("agent-bridge")

ELEVATED_PORT = 9281
TASK_NAME = "agent-bridge-elevated"
_SUBDIR = "elevated"


def elevated_dir() -> Path:
    """The sub-daemon's isolated config/state dir (``<primary>/elevated``)."""
    d = config_dir() / _SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _venv_python() -> str:
    """Path to the agent-bridge venv python that runs the daemon."""
    import sys

    # When running from the installed binstub venv, sys.executable is it.
    return sys.executable


def _seed_config(port: int) -> Path:
    """Write the sub-daemon's isolated config.yaml (own port + db; shared
    topologies so the same agents are discovered). Returns the elevated dir."""
    ed = elevated_dir()
    primary = load_config()
    data = primary.model_dump(exclude_defaults=False)
    data["port"] = port
    data["bind"] = "127.0.0.1"
    data["db_path"] = str(ed / "sessions.db")
    (ed / "config.yaml").write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False)
    )
    return ed


def _write_launcher(ed: Path, port: int) -> Path:
    """Write the task-action launcher.cmd that runs the elevated daemon."""
    py = _venv_python()
    log_path = ed / "elevated-daemon.log"
    launcher = ed / "launcher.cmd"
    launcher.write_text(
        "@echo off\r\n"
        f'set "AGENT_BRIDGE_CONFIG_DIR={ed}"\r\n'
        f'"{py}" -m agent_bridge start --port {port} --bind 127.0.0.1 '
        f'>> "{log_path}" 2>&1\r\n',
        encoding="ascii",
    )
    return launcher


def _write_bootstrap(ed: Path, launcher: Path, *, action: str) -> Path:
    """Write a privileged bootstrap .cmd (create+run, or end+delete the task)."""
    if action == "start":
        body = (
            "@echo off\r\n"
            f'schtasks /create /tn "{TASK_NAME}" '
            f'/tr "{launcher}" /sc ONCE /st 00:00 /RL HIGHEST /f\r\n'
            f'schtasks /run /tn "{TASK_NAME}"\r\n'
        )
        name = "bootstrap-start.cmd"
    else:
        body = (
            "@echo off\r\n"
            f'schtasks /end /tn "{TASK_NAME}" 2>nul\r\n'
            f'schtasks /delete /tn "{TASK_NAME}" /f 2>nul\r\n'
        )
        name = "bootstrap-stop.cmd"
    p = ed / name
    p.write_text(body, encoding="ascii")
    return p


def _run_elevated(script: Path) -> int:
    """Run a .cmd elevated via a single UAC prompt; wait for completion."""
    ps = (
        f"$p = Start-Process -FilePath '{script}' -Verb RunAs -WindowStyle Hidden "
        f"-PassThru -Wait; exit $p.ExitCode"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.warning(
            "elevated bootstrap exit=%s stderr=%s",
            proc.returncode, (proc.stderr or "").strip(),
        )
    return proc.returncode


def is_process_elevated() -> bool:
    """True if the current process holds an elevated (admin) token.

    Windows only; returns ``False`` on other platforms (and on any failure).
    This is the recursion guard for routing: the elevated sub-daemon runs
    elevated, so it spawns ``requires_admin`` agents locally instead of
    relaying back into itself (see ``relay_spawn_command``).
    """
    import sys

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relay_applicable(requires_admin: bool) -> bool:
    """True if an elevated agent should be routed to the sub-daemon relay.

    Only on Windows, only for ``requires_admin`` agents, and only when *this*
    daemon is not already elevated (an elevated daemon spawns such agents
    locally -- relaying would recurse).
    """
    import sys

    if not requires_admin or sys.platform != "win32":
        return False
    return not is_process_elevated()


def relay_spawn_command(
    agent_name: str, *, token: str, port: int = ELEVATED_PORT
) -> list[str]:
    """Build the ``acp-connect`` relay command for an elevated agent.

    The primary (non-elevated) bridge spawns this as a ``type="command"``
    target; the relay shuttles stdio NDJSON to the elevated sub-daemon's
    ``WS /acp/<agent>`` endpoint, which drives the elevated copilot.
    Invoked via ``<python> -m agent_bridge`` (not the ``.cmd`` binstub) so
    forwarded arguments are not mangled by cmd.exe.
    """
    import sys

    url = f"ws://127.0.0.1:{port}/acp/{agent_name}"
    return [
        sys.executable, "-m", "agent_bridge", "acp-connect",
        url, "--token", token, "--stdio",
    ]


def is_up(port: int = ELEVATED_PORT, timeout: float = 1.0) -> bool:
    """True if a bridge answers /health on the loopback port."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def read_token() -> str | None:
    """Read the sub-daemon's bearer token from its isolated auth.yaml."""
    auth = elevated_dir() / "auth.yaml"
    if not auth.exists():
        return None
    try:
        data = yaml.safe_load(auth.read_text()) or {}
        tok = data.get("token")
        return str(tok) if tok else None
    except Exception:
        return None


def ensure_running(port: int = ELEVATED_PORT, *, wait: float = 60.0) -> str:
    """Ensure the elevated sub-daemon is up; return its bearer token.

    Idempotent: if already serving on the port, just returns the token. Otherwise
    seeds config, registers+runs the elevated scheduled task (one UAC), and polls
    /health until ready.
    """
    if is_up(port):
        tok = read_token()
        if tok:
            return tok

    ed = _seed_config(port)
    launcher = _write_launcher(ed, port)
    bootstrap = _write_bootstrap(ed, launcher, action="start")

    log.info("Launching elevated sub-daemon on 127.0.0.1:%d (expect a UAC prompt)", port)
    _run_elevated(bootstrap)

    deadline = time.time() + wait
    while time.time() < deadline:
        if is_up(port):
            tok = read_token()
            if tok:
                log.info("Elevated sub-daemon ready on 127.0.0.1:%d", port)
                return tok
        time.sleep(1.0)
    raise RuntimeError(
        f"Elevated sub-daemon did not become ready on port {port} within {wait}s "
        f"(see {ed / 'elevated-daemon.log'})"
    )


def stop(port: int = ELEVATED_PORT) -> None:
    """Stop and deregister the elevated sub-daemon (one UAC prompt)."""
    ed = elevated_dir()
    launcher = ed / "launcher.cmd"
    bootstrap = _write_bootstrap(ed, launcher, action="stop")
    _run_elevated(bootstrap)


def status(port: int = ELEVATED_PORT) -> dict:
    """Return a small status dict for the sub-daemon."""
    info: dict = {"port": port, "up": is_up(port), "config_dir": str(elevated_dir())}
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"],
            capture_output=True, text=True,
        )
        info["task_registered"] = out.returncode == 0
    except OSError:
        info["task_registered"] = False
    info["agents"] = _list_agents(port) if info["up"] else []
    return info


def _list_agents(port: int) -> list[str]:
    """List agent names the sub-daemon exposes (best-effort)."""
    tok = read_token()
    if not tok:
        return []
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/agents",
        headers={"Authorization": f"Bearer {tok}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read())
        agents = data.get("agents", data) if isinstance(data, dict) else data
        return [a.get("name", "?") for a in agents] if isinstance(agents, list) else []
    except Exception:
        return []
