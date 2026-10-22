"""Synthetic updater behind the real Manager request and participant boundaries."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import update_driver
from worktree_manager import __main__ as manager
from worktree_manager import engine_client
from worktree_manager import update_driver_cli as boundary


def main() -> int:
    boundary.source_identity = lambda: {"repo": "fixture-source", "ref": "fixed"}
    engine_client.installed_engine_command = lambda: ["synthetic-marker-provider"]
    if sys.argv[1] in {"submit", "submit-only"}:
        original = update_driver.runtime_command

        def command(module, args=()):
            if module == "worktree_manager":
                return (sys.executable, str(Path(__file__).resolve()), "execute", *args), {}
            return original(module, args)

        update_driver.runtime_command = command
        if sys.argv[1] == "submit-only":
            update_driver.ensure_worker = lambda *args, **kwargs: None
            return manager.main(["update", "--detach", *sys.argv[2:]])
        return manager.main(["update", *sys.argv[2:]])

    def execute(rest):
        events = Path(os.environ["UPDATE_DRIVER_TEST_EVENTS"])
        with events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "phase": "start", "pid": os.getpid(), "time": time.time(),
                "provider": engine_client.engine_base_command(),
                "arguments": list(rest),
            }) + "\n")
            stream.flush()
        print("synthetic canonical updater started", file=sys.stderr, flush=True)
        time.sleep(0.1)
        with events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"phase": "end", "pid": os.getpid(), "time": time.time()}) + "\n")
        return 6

    manager._cmd_update = execute
    return manager.main(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
