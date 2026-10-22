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
    if sys.argv[1] == "manager":
        sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "worktree-manager" / "src"))
        from worktree_manager import __main__ as manager, engine_client

        def return_to_engine(rest):
            (scope / "manager-entered").write_text(
                os.environ.get("AGENT_WORKTREES_UPDATE_CONTEXT", ""), encoding="utf-8",
            )
            project, flags = manager._extract_project(rest)
            engine_client.set_engine_command([sys.executable, str(Path(__file__).resolve()), "return"])
            return engine_client.run_engine_passthrough(
                project, ["update", "--no-manager", *flags],
            )

        manager._cmd_update = return_to_engine
        return manager.main(sys.argv[2:])
    if sys.argv[1] == "submit":
        original = update_driver.runtime_command

        def command(module, args=()):
            argv, environment = original(module, args)
            if module == "agent_worktrees":
                argv = (sys.executable, str(Path(__file__).resolve()), "execute", *args)
            return argv, environment

        update_driver.runtime_command = command
        update_driver.ensure_worker = lambda *args, **kwargs: SimpleNamespace()
        bypass = [] if os.environ.get("UPDATE_DRIVER_TEST_MANAGER") == "1" else ["--no-manager"]
        return engine.main(["--project", "example", "update", *bypass, "--detach"])

    def execute(args):
        (scope / "result.json").write_text(json.dumps({
            "anchor": str(Path.cwd()),
            "invocation": str(engine._INVOCATION_CWD),
            "update_context": str(update_cli._invocation_update_context()),
        }), encoding="utf-8")
        return 0

    update_cli.cmd_update = execute
    if sys.argv[1] == "execute" and os.environ.get("UPDATE_DRIVER_TEST_MANAGER") == "1":
        canonical = update_cli.cmd_update
        update_cli._usable_worktree_manager = lambda: [
            sys.executable, str(Path(__file__).resolve()), "manager",
        ]
        engine._usable_worktree_manager = update_cli._usable_worktree_manager

        def handoff(args):
            handed_off = update_cli.manager_handoff(args)
            return handed_off if handed_off is not None else canonical(args)

        update_cli.cmd_update = handoff
    return engine.main(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
