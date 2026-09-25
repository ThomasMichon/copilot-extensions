"""Generic state and monitor loop for detached SSH reverse-forward keepers."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .locks import pid_alive


def _safe(key: str) -> str:
    return (re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-") or "target")[:120]


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or not pid_alive(pid):
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


class KeeperStore:
    """Small JSON state store for one keeper family."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)

    def state_path(self, key: str) -> Path:
        return self.state_dir / f"{_safe(key)}.json"

    def read(self, key: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self.state_path(key).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def write(self, key: str, payload: dict[str, Any]) -> None:
        _atomic_write_json(self.state_path(key), payload)

    def remove(self, key: str) -> None:
        self.state_path(key).unlink(missing_ok=True)

    def alive(self, key: str) -> bool:
        state = self.read(key)
        try:
            return bool(state and pid_alive(int(state.get("pid") or 0)))
        except (TypeError, ValueError):
            return False

    def stop(self, key: str) -> bool:
        state = self.read(key)
        if not state:
            return False
        try:
            pid = int(state.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        alive = pid_alive(pid) if pid > 0 else False
        if alive:
            _terminate_pid(pid)
        self.remove(key)
        return alive


def spawn_keeper(
    argv: list[str],
    env: dict[str, str],
    state: dict[str, Any],
    *,
    popen: Any = subprocess.Popen,
    popen_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Spawn a detached keeper and return the state with its pid."""
    proc = popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        **(popen_kwargs or {}),
    )
    return {**state, "pid": int(proc.pid), "started_at": time.time()}


async def run_supervised_loop(
    forwards: list[Any],
    *,
    session_alive: Callable[[], bool],
    write_state: Callable[[], None],
    remove_state: Callable[[], None],
    probe_interval: float,
    startup_grace: float,
) -> int:
    """Start forwards, wait for the session, then exit when it disappears."""
    write_state()
    try:
        for forward in forwards:
            await forward.start()
        startup_deadline = asyncio.get_running_loop().time() + max(0.0, startup_grace)
        while True:
            if await asyncio.to_thread(session_alive):
                break
            if asyncio.get_running_loop().time() >= startup_deadline:
                return 0
            await asyncio.sleep(min(5.0, max(1.0, probe_interval)))
        while True:
            await asyncio.sleep(max(1.0, probe_interval))
            if not await asyncio.to_thread(session_alive):
                return 0
    finally:
        for forward in reversed(forwards):
            await forward.stop()
        remove_state()
