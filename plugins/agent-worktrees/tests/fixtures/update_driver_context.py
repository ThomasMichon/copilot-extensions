"""Actual engine routing against synthetic registry and canonical updater inputs."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import update_driver
from agent_worktrees import __main__ as engine
from agent_worktrees import front_door_cli, update_cli


def main():
    scope = Path(os.environ["AGENT_HOME"])
    front_door_cli.inst.read_projects_registry = lambda: {
        "projects": {"example": {"anchor": str(scope / "anchor")}},
    }
    if sys.argv[1] == "submit":
        original = update_driver.runtime_command

        def command(module, args=()):
            argv, environment = original(module, args)
            if module == "agent_worktrees":
                argv = (sys.executable, str(Path(__file__).resolve()), "execute", *args)
            return argv, environment

        update_driver.runtime_command = command
        update_driver.ensure_worker = lambda *args, **kwargs: SimpleNamespace()
        return engine.main(["--project", "example", "update", "--no-manager", "--detach"])

    def execute(args):
        (scope / "result.json").write_text(json.dumps({
            "anchor": str(Path.cwd()),
            "invocation": str(engine._INVOCATION_CWD),
            "update_context": str(update_cli._invocation_update_context()),
        }), encoding="utf-8")
        return 0

    update_cli.cmd_update = execute
    return engine.main(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
