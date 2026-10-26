"""Finite qualification command; not an installer or a substitute cadence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from . import __version__
from .automatic_updates_policy import atomic_json

# Imported by the fixture main before its unchanged UpdateRun is constructed.
if "WORKTREE_MANAGER_ROOT" in os.environ:
    fixture_root = Path(os.environ["WORKTREE_MANAGER_ROOT"])
    os.environ["COPILOT_EXTENSIONS_UPDATE_HOME"] = str(
        fixture_root / "probe" / "update-telemetry"
    )
    if os.environ.get("WORKTREE_MANAGER_AUTOMATIC_UPDATES_UNIT"):
        from agent_procutil.daemon_instances import TEST_CONTEXT_ENV
        from .automatic_updates_startup import checked_path

        binding = checked_path(fixture_root / "probe" / "native-lifecycle.json")
        overlay = json.loads(binding.read_text(encoding="utf-8"))
        if (
            not isinstance(overlay, dict) or set(overlay) != {TEST_CONTEXT_ENV}
            or not isinstance(overlay[TEST_CONTEXT_ENV], str) or not overlay[TEST_CONTEXT_ENV]
        ):
            raise RuntimeError("native fixture has no exact disposable lifecycle declaration")
        os.environ.update(overlay)
    if sys.argv[1:2] == ["update"]:
        # Native supervision retains its real home; installation admission must not.
        execution_home = fixture_root / "home"
        execution_home.mkdir(exist_ok=True)
        os.environ.update({
            "HOME": str(execution_home), "USERPROFILE": str(execution_home),
            "XDG_CONFIG_HOME": str(execution_home / ".config"),
            "XDG_CACHE_HOME": str(execution_home / ".cache"),
        })
        from update_driver import default_root

        if not default_root().is_relative_to(execution_home):
            raise RuntimeError("fixture update admission escaped its private execution home")


def execute() -> int:
    root = Path(os.environ["WORKTREE_MANAGER_ROOT"])
    directory = root / "probe"
    directory.mkdir(exist_ok=True)
    active = directory / "active-command"
    fd = os.open(active, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    number = len(list(directory.glob("cycle-*-begin.json"))) + 1
    identity = {
        "version": __version__, "runtime": str(Path(sys.prefix).resolve()),
        "pid": os.getpid(), "number": number,
    }
    try:
        atomic_json(
            directory / f"cycle-{number:03d}-begin.json",
            {**identity, "monotonic": time.monotonic()},
        )
        deadline = time.monotonic() + 60
        while not (directory / "release").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("finite qualification command was not released within 60 seconds")
            time.sleep(0.05)
        atomic_json(
            directory / f"cycle-{number:03d}-end.json",
            {**identity, "monotonic": time.monotonic()},
        )
        return 0
    finally:
        active.unlink()
