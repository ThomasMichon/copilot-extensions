"""Synthetic updater behind the real Manager request and participant boundaries."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType

import update_driver
from worktree_manager import engine_client
from worktree_manager import update_driver_cli as boundary


def main() -> int:
    boundary.source_identity = lambda: {"repo": "fixture-source", "ref": "fixed"}
    engine_client.installed_engine_command = lambda: ["synthetic-marker-provider"]
    engine_client.accept_inherited_engine_command()
    if sys.argv[1] in {"submit", "submit-only"}:
        original = update_driver.runtime_command

        def command(module, args=()):
            if module == "worktree_manager":
                return (sys.executable, str(Path(__file__).resolve()), "execute", *args), {}
            return original(module, args)

        update_driver.runtime_command = command
        if sys.argv[1] == "submit-only":
            update_driver.ensure_worker = lambda *args, **kwargs: None
            return boundary.cmd_update_request(["--detach", *sys.argv[2:]])
        return boundary.cmd_update_request(sys.argv[2:])

    def execute(rest):
        events = Path(os.environ["UPDATE_DRIVER_TEST_EVENTS"])
        with events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "phase": "start", "pid": os.getpid(), "time": time.time(),
                "provider": engine_client.engine_base_command(),
                "arguments": list(rest),
                "acquisition_budget": os.environ.get(
                    "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC",
                ),
            }) + "\n")
            stream.flush()
        print("synthetic canonical updater started", file=sys.stderr, flush=True)
        result = 6
        if os.environ.get("UPDATE_DRIVER_TEST_ENGINE_HANDOFF"):
            from agent_procutil.owned_process import run_owned

            os.environ.update(boundary.handoff_environment(["update", "--no-manager"]))
            child = run_owned(
                [sys.executable, str(Path(__file__).with_name("update_driver_engine.py"))],
                timeout=30,
            )
            if child.stderr:
                print(child.stderr, file=sys.stderr, end="")
            result = child.returncode
        time.sleep(0.1)
        with events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"phase": "end", "pid": os.getpid(), "time": time.time()}) + "\n")
        return result

    # Only the product body is synthetic; queue, worker and custody remain real.
    manager = ModuleType("worktree_manager.__main__")
    manager._cmd_update = execute
    sys.modules[manager.__name__] = manager
    assert sys.argv[2] == "update"
    return boundary.cmd_update_request(sys.argv[3:])


if __name__ == "__main__":
    raise SystemExit(main())
