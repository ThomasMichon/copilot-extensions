"""Actual engine routing against synthetic registry and canonical updater inputs."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

repository = Path(__file__).resolve().parents[4]
# The real Manager drops parent Python overrides. This source-only fixture
# supplies its own fixed graph on every hop, as an installed runtime would.
sys.path[:0] = [
    str(repository / "plugins" / "agent-worktrees" / "src"),
    *(str(path) for path in sorted((repository / "libs").glob("*/src"))),
]

import update_driver
from agent_worktrees import __main__ as engine
from agent_worktrees import front_door_cli, update_cli


def selected_inputs(scope):
    from agent_worktrees import reconcile, update_acquisition, update_integration, update_runtime

    payloads = scope / "payloads"
    owner, peer = payloads / "agent-worktrees", payloads / "agent-example"
    for payload in (owner, peer):
        (payload / "scripts").mkdir(parents=True)
        (payload / "plugin.json").write_text(json.dumps({
            "name": payload.name, "version": "2", "runtimeScope": "universal",
        }))
        for suffix in ("ps1", "sh"):
            (payload / "scripts" / f"install.{suffix}").write_text("unused")
    engine._registered_plugin_targets = lambda: {
        peer.name: update_runtime._RegisteredPluginTarget(
            scope, update_runtime._PluginActivation.ACTIVE,
        ),
    }
    engine._find_installed_plugin_dir = lambda: owner
    reconcile.core_installed_payload_dir = lambda name: peer
    reconcile.runtime_installer_environment = lambda name, payload: ({}, scope / name)
    reconcile.runtime_deployed_version = lambda *a, **kw: "1"
    update_runtime._machine_gate_blocks = lambda *a: False

    def forbidden(*args, **kwargs):
        raise AssertionError("selected preflight entered an unselected executor")

    update_acquisition.acquire = forbidden
    update_integration.finish = forbidden
    update_runtime._run_human_installer = forbidden
    update_cli._run_engine_installer = forbidden
    update_cli._usable_worktree_manager = forbidden
    engine._usable_worktree_manager = forbidden
    engine._fast_forward_project_anchors = forbidden
    engine._prune_stale_pivots_after_update = forbidden


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
            engine_client.set_engine_command([
                sys.executable, str(Path(__file__).resolve()), "return",
            ])
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
                argv = (argv[0], str(Path(__file__).resolve()), "execute", *args)
            return argv, environment

        update_driver.runtime_command = command
        update_driver.ensure_worker = lambda *args, **kwargs: SimpleNamespace()
        bypass = [] if os.environ.get("UPDATE_DRIVER_TEST_MANAGER") == "1" else ["--no-manager"]
        sources = os.environ.get("UPDATE_DRIVER_TEST_SOURCES")
        return engine.main([
            "--project", "example", "update", *bypass, "--detach",
            *(["--runtime-sources", sources] if sources is not None else []),
            *sys.argv[2:],
        ])

    actual_update = update_cli.cmd_update
    def execute(args):
        sources = getattr(args, "runtime_sources", None)
        result = {
            "anchor": str(Path.cwd()),
            "invocation": str(engine._INVOCATION_CWD),
            "update_context": str(update_cli._invocation_update_context()),
            **({"runtime_sources": sources} if sources is not None else {}),
        }
        code = 0
        if getattr(args, "phase", None):
            selected_inputs(scope)
            code = actual_update(args)
            result.update(phase=args.phase, plugins=args.plugin, full_update=False, exit_code=code)
        (scope / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return code

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
