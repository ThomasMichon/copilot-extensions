"""Optional read-only Worktrees lookup, retaining explicit installation fences."""

from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

from agent_procutil.owned_process import run_caller_owned

from ._peer_launch import ContextRefused, launch_prefix, validate_owner


def validate_context() -> dict | None:
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if not raw:
        return None
    try:
        pointer = json.loads(raw).get("installReceipt") if raw.lstrip().startswith("{") else raw
        if not isinstance(pointer, str) or not pointer:
            raise ValueError("Explicit context must name an installation receipt")
        root = Path(os.environ.get("AGENT_RT_ROOT") or Path(pointer).parent)
        return validate_owner("agent-ssh", root, raw)
    except (OSError, ValueError, ImportError) as exc:
        raise ContextRefused("agent-ssh installation context refused") from exc


def state_root(cwd: Path, own: dict | None) -> tuple[Path | None, str]:
    started = time.monotonic()
    payload = own.get("payloadRoot") if own else os.environ.get("AGENT_SSH_PAYLOAD_ROOT")
    launcher = Path(payload) / "scripts" / "snapshot_runtime.py" if payload else None
    if launcher is not None and launcher.is_file():
        try:
            spec = importlib.util.spec_from_file_location("_ssh_snapshot_dispatch", launcher)
            if spec is None or spec.loader is None:
                raise ValueError("Snapshot dispatch unavailable")
            adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter)
            argv, environment = adapter.worktrees_command(Path(payload), own)
        except FileNotFoundError as exc:
            if own is not None:
                peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
                if peer.exists() or peer.is_symlink():
                    raise ContextRefused("present same-cell Worktrees is incomplete") from exc
            return None, "unavailable"
        except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
            raise ContextRefused("optional Worktrees installation context refused") from exc
        remaining = 5 - (time.monotonic() - started)
        if remaining <= 0:
            if own is not None:
                raise ContextRefused("same-cell Worktrees resolution timed out")
            return None, "unavailable"
        return _read_state_root(argv, environment, cwd, own, timeout=remaining, append_args=False)
    if own is not None:
        peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
        if not peer.exists() and not peer.is_symlink():
            return None, "unavailable"
        argv = launch_prefix(
            "agent-ssh",
            Path(own["pluginRoot"]),
            os.environ["COPILOT_EXTENSIONS_CONTEXT"],
            "agent-worktrees",
        )
        environment = os.environ.copy()
    else:
        return _legacy_state_root(cwd)
    return _read_state_root(argv, environment, cwd, own)


def _legacy_state_root(cwd: Path) -> tuple[Path | None, str]:
    """Compatibility for a caller without an attributable payload launcher."""
    command = shutil.which("agent-worktrees")  # marketplace-isolation: allow no-context legacy optional peer; explicit context uses canonical peer-launch
    if not command:
        return None, "unavailable"
    argv = [command]
    if os.name == "nt" and Path(command).suffix.casefold() == ".ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not shell:
            return None, "unavailable"
        argv = [shell, "-NoProfile", "-File", command]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT"}
    }
    environment["AGENT_WORKTREES_NO_SELFPROVISION"] = "1"
    return _read_state_root(argv, environment, cwd, None)


def _read_state_root(argv, environment, cwd, own, *, timeout=5, append_args=True):
    try:
        result = run_caller_owned(
            [*argv, "state-root", "--json"] if append_args else argv,
            cwd=cwd,
            env=environment,
            timeout=timeout,
            max_stdout_bytes=65536,
            max_stderr_bytes=65536,
        )
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError) as exc:
        if own is not None:
            raise ContextRefused("same-cell Worktrees invocation failed") from exc
        return None, "unavailable"
    if result.returncode == 126:
        raise ContextRefused("optional Worktrees installation context refused")
    if result.returncode == 127:
        return None, "unavailable"
    if result.returncode not in (0, 3) or len(result.stdout.encode("utf-8")) > 65536:
        return None, "error"
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or type(value.get("requires_external")) is not bool:
            raise ValueError("invalid state-root response")
        if result.returncode == 3 and not (
            value["requires_external"] and value.get("bound") is False
        ):
            raise ValueError("unexpected state-root failure response")
        if not value["requires_external"]:
            return None, "not-required"
        if value.get("bound") is not True:
            return None, "unbound"
        path = Path(value["state_root"])
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("invalid state root")
        return path, "bound"
    except (KeyError, TypeError, ValueError):
        return None, "error"
