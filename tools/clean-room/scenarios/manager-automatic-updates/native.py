"""Explicit native fixture activation; no native operation is implicit."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys


class UnsupportedNative(RuntimeError):
    pass


def preflight() -> None:
    if sys.platform == "win32":
        from windows_native import preflight as windows_preflight

        windows_preflight()
        return
    if not sys.platform.startswith("linux") or os.getuid() == 0:
        raise UnsupportedNative("native fixture requires an unelevated Linux/WSL user manager")
    result = systemctl("show", "--property=Version")
    if result.returncode:
        raise UnsupportedNative(f"user-manager-unavailable: {result.stderr.strip()}")


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=20, check=False,
    )


def registration(root: Path) -> dict:
    module = importlib.import_module("worktree_manager.automatic_updates_autostart")
    result = module.register_autostart(root)
    if result.get("activation") == "blocked":
        raise RuntimeError(f"native registration blocked: {result.get('blockers')}")
    result = module.activate_autostart(root)
    if result.get("activation") == "blocked":
        raise RuntimeError(f"native activation blocked: {result.get('blockers')}")
    return result


def unit_name(root: Path) -> str:
    module = importlib.import_module("worktree_manager.automatic_updates_autostart")
    status = module.inspect_autostart(root)
    if not isinstance(status.get("unit"), str):
        raise RuntimeError(f"native fixture identity unavailable: {status.get('blockers')}")
    return status["unit"]


def fields(unit: str) -> dict[str, str]:
    result = systemctl(
        "show", unit, "--property=MainPID", "--property=ControlGroup",
        "--property=NRestarts", "--property=ExecMainStatus", "--property=ActiveState",
    )
    if result.returncode:
        raise RuntimeError(f"cannot inspect owned native fixture: {result.stderr.strip()}")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def assert_mainpid(unit: str, status: dict, root: Path) -> dict[str, str]:
    values = fields(unit)
    if int(values.get("MainPID", "0")) != status["pid"]:
        raise AssertionError("native MainPID is not the health-verified Python worker")
    from worktree_manager.automatic_updates_startup import process_snapshot
    from fixtures import command

    identity = process_snapshot(status["pid"])
    slot = Path(status["runtime"])
    if slot.parent != root / "versions":
        raise AssertionError("native worker does not use this fixture's retained generation")
    expected = tuple(command(
        root, ["automatic-updates", "run", "--root", str(root)], version=slot.name,
    )[1:])
    if identity[2][1:] != expected:
        raise AssertionError("native MainPID is not the retained worker argv")
    group = values.get("ControlGroup", "")
    if not group.endswith("/" + unit) or group not in identity[3]:
        raise AssertionError("native worker does not belong to the exact unit cgroup")
    return values


def retire(root: Path, unit: str) -> None:
    """Only after policy drain/verified process exit; never stop an active update."""
    current = fields(unit)
    if current.get("ActiveState") in {"active", "activating", "deactivating", "reloading"}:
        raise RuntimeError("fixture native service did not drain; automatic stop refused")
    module = importlib.import_module("worktree_manager.automatic_updates_autostart")
    result = module.remove_autostart(root)
    if result.get("registered") or result.get("activation") == "blocked":
        raise RuntimeError(f"fixture unit retirement blocked: {result.get('blockers')}")
