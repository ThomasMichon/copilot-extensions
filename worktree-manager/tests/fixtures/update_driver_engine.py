"""Exercise the unchanged engine adapter with only its installer body replaced."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import update_driver


def main() -> int:
    source = (
        Path(__file__).resolve().parents[3]
        / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "update_driver_cli.py"
    )
    package = ModuleType("engine_participant_fixture")
    package.__path__ = [str(source.parent)]
    config = ModuleType(f"{package.__name__}.config")
    installer = ModuleType(f"{package.__name__}.update_cli")
    installer.update_options = SimpleNamespace(validate_auth_flags=lambda args: None)

    def execute(args):
        requests = update_driver.status()
        assert len(requests) == 1 and requests[0].state == "running"
        request = requests[0]
        with Path(os.environ["UPDATE_DRIVER_TEST_EVENTS"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "phase": "engine", "pid": os.getpid(),
                "context_keys": sorted(dict(request.spec.context)),
                "no_manager": args.no_manager,
            }) + "\n")
        return 6

    installer.cmd_update = execute
    for module in (package, config, installer):
        sys.modules[module.__name__] = module
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.update_driver_cli", source,
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter.cmd_update_request(SimpleNamespace(no_manager=True))


if __name__ == "__main__":
    raise SystemExit(main())
