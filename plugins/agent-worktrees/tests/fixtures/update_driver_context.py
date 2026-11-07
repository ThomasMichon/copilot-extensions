"""Actual engine routing against synthetic registry and canonical updater inputs."""

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

repository = Path(__file__).resolve().parents[4]
prepared_mode = os.environ.get("UPDATE_DRIVER_TEST_RUNTIME_MODE", "source")
if prepared_mode not in {"source", "prepared"}:
    raise ValueError("unknown routing fixture runtime mode")
prepared = (
    json.loads(Path(os.environ["UPDATE_DRIVER_TEST_PREPARED_RUNTIME"]).read_text(encoding="utf-8"))
    if prepared_mode == "prepared" else None
)
if prepared is None and sys.argv[1:2] != ["prepare"]:
    # Source-routing compatibility is not prepared-runtime or cold-bootstrap proof.
    sys.path[:0] = json.loads(os.environ["UPDATE_DRIVER_TEST_IMPORT_PATH"])


def phase(name, **details):
    path = os.environ.get("UPDATE_DRIVER_TEST_PHASE_LOG")
    if path:
        with Path(path).open("a", encoding="utf-8") as logfile:
            logfile.write(json.dumps({
                "time": time.monotonic(), "role": sys.argv[1],
                "phase": name, "pid": os.getpid(), **details,
            }) + "\n")


phase("import-graph-ready")


def diagnose_installed_probe(engine, destination):
    """Observe the unchanged native probe through its existing child owner."""
    namespace = engine._prepare_installed.__globals__
    original = namespace["_child"]

    def child(argv, **kwargs):
        def delegated(command):
            started = time.monotonic()

            def record(kind, **fields):
                try:
                    with destination.open("a", encoding="utf-8") as logfile:
                        logfile.write(json.dumps({
                            "kind": kind, "time": time.monotonic(),
                            "elapsed": time.monotonic() - started,
                            "pid": os.getpid(), **fields,
                        }) + "\n")
                        logfile.flush()
                except (OSError, ValueError, TypeError):
                    # Diagnostics must not replace the native return or exception.
                    pass

            record(
                "native-child-entry", argv=list(map(str, command)),
                timeout=kwargs.get("timeout"), cwd=str(kwargs.get("cwd")),
                candidate=str(kwargs.get("candidate")),
            )
            try:
                result = original(command, **kwargs)
            except BaseException as error:
                record("native-child-error", exception=type(error).__name__, detail=str(error))
                raise
            record("native-child-exit", returncode=result.returncode)
            return result

        if (
            len(argv) == 10 and list(argv[1:4]) == ["-I", "-B", "-c"]
            and list(argv[6:8]) == [
                "worktree_manager.runtime_candidate", "prepare_runtime_candidate",
            ]
        ):
            with destination.open("a", encoding="utf-8") as logfile:
                logfile.write(json.dumps({
                    "kind": "original-native-argv", "argv": list(map(str, argv)),
                    "time": time.monotonic(),
                }) + "\n")
            before = {
                "for name, wheel in (source_wheels or {}).items():": (
                    "_routing_beacon('wheel-verification-start', "
                    "wheel_count=0 if source_wheels is None else "
                    "len(source_wheels) if isinstance(source_wheels, dict) else None, "
                    "member_count=0 if source_wheels is None else "
                    "sum(len(w.get('files', {})) for w in (source_wheels or {}).values() "
                    "if isinstance(w, dict) and isinstance(w.get('files'), dict)) "
                    "if isinstance(source_wheels, dict) else None, "
                    "receipt_type=type(source_wheels).__name__)"
                ),
                "module = importlib.import_module(sys.argv[2])": (
                    "_routing_beacon('wheel-verification-completed')\n"
                    "_routing_beacon('module-import-start')"
                ),
                "getattr(module, sys.argv[3])(json.loads(sys.argv[4]))": (
                    "_routing_beacon('callback-start')"
                ),
            }
            after = {
                "module = importlib.import_module(sys.argv[2])": (
                    "_routing_beacon('module-import-completed')"
                ),
                "getattr(module, sys.argv[3])(json.loads(sys.argv[4]))": (
                    "_routing_beacon('callback-completed')\n"
                    "_routing_fault.cancel_dump_traceback_later()"
                ),
            }
            probe = argv[4]
            for anchor in before:
                if probe.splitlines().count(anchor) != 1:
                    raise RuntimeError("native installed probe differs from diagnostic anchors")
            header = f"""
import faulthandler as _routing_fault, json as _routing_json
import os as _routing_os, time as _routing_time
_routing_log = open({str(destination)!r}, 'a', encoding='utf-8', buffering=1)
def _routing_beacon(phase, **fields):
    _routing_log.write(_routing_json.dumps(dict(
        phase=phase, time=_routing_time.monotonic(), pid=_routing_os.getpid(), **fields
    )) + '\\n')
    _routing_log.flush()
_routing_beacon('probe-entered')
_routing_fault.enable(file=_routing_log)
_routing_fault.dump_traceback_later(30, repeat=True, file=_routing_log)
"""
            lines = []
            for line in probe.splitlines(keepends=True):
                anchor = line.rstrip("\r\n")
                if anchor in before:
                    lines.append(before[anchor] + "\n")
                lines.append(line)
                if anchor in after:
                    lines.append(after[anchor] + "\n")
            command = list(argv)
            command[4] = header + "".join(lines)
            return delegated(command)
        return delegated(argv)

    namespace["_child"] = child
    return lambda: namespace.__setitem__("_child", original)


def prepare(directory):
    """Construct real unpublished generations before the routing item timers."""
    import importlib.util
    import shutil
    import subprocess
    import tomllib
    from contextlib import ExitStack

    from agent_procutil import no_window_kwargs

    sys.path.insert(0, str(repository / "worktree-manager" / "src"))
    from worktree_manager import self_install

    manager = self_install._runtime_installer()
    scripts = repository / "plugins" / "agent-worktrees" / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("_routing_worktrees_install", scripts / "runtime_install.py")
    worktrees = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worktrees)
    uv = Path(shutil.which("uv"))
    base = Path(getattr(sys, "_base_executable", sys.executable))
    document = {"purpose": "prepared ordinary routing, not cold-first-touch proof"}

    def run(argv):
        result = subprocess.run(
            list(map(str, argv)), capture_output=True, text=True, check=False,
            timeout=180, **no_window_kwargs(),
        )
        if result.returncode:
            raise RuntimeError(f"preparation failed: {result.stdout}\n{result.stderr}")
        return result.stdout

    with ExitStack() as retained:
        diagnostics = os.environ.get("UPDATE_DRIVER_TEST_INSTALLED_PROBE_DIAGNOSTICS")
        if diagnostics:
            import faulthandler

            controller = retained.enter_context(open(
                diagnostics + ".controller.log", "a", encoding="utf-8",
            ))
            retained.callback(faulthandler.cancel_dump_traceback_later)
            faulthandler.dump_traceback_later(30, repeat=True, file=controller)
            retained.callback(diagnose_installed_probe(manager.engine, Path(diagnostics)))
        phase("manager-construction-start")
        manager_root = directory / "manager"
        manager_ready = retained.enter_context(manager.candidate(
            repository / "worktree-manager", manager_root,
            assemble=self_install._copy_payload, uv=uv, python=base,
        ))
        manager.runtime.mark_complete(
            manager_root, manager_ready.slot.name, payload_hash=manager_ready.payload_hash,
        )
        phase("manager-construction-completed")
        phase("engine-construction-start")
        engine_root = directory / "engine"
        plugin = repository / "plugins" / "agent-worktrees"
        version = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        engine_ready = retained.enter_context(worktrees.engine.numbered_candidate(
            engine_root, version, plugin=plugin, uv=uv, base_python=base,
            health_module="agent_worktrees.runtime_candidate",
            health_callable="verify_runtime_candidate",
            construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
            construction_data=worktrees._source_provenance(plugin, version),
            process_retention=True,
        ))
        worktrees.runtime.mark_complete(
            engine_root, engine_ready.slot.name, payload_hash=engine_ready.payload_hash,
        )
        phase("engine-construction-completed")
        for name, owner, root, ready in (
            ("manager", manager, manager_root, manager_ready),
            ("engine", worktrees, engine_root, engine_ready),
        ):
            python = owner.runtime.slot_python(root, ready.slot.name)
            probe = """
import json, sys, agent_procutil, update_driver
print(json.dumps({
    "script": update_driver.runtime_script_command(sys.argv[2]),
    "worker": update_driver.runtime_command("update_driver", ["worker"]),
    "cache": agent_procutil.python_cache_args(sys.argv[1]),
    "prefix": sys.prefix,
}))
"""
            selected = json.loads(run([
                python, "-I", "-B", "-c", probe, root, Path(__file__).resolve(),
            ]))
            assert Path(selected["prefix"]).resolve() == ready.slot.resolve()
            # Use the slot's own interpreter across normal Python-env sanitization.
            for command in ("script", "worker"):
                selected[command][0][0] = str(python)
                selected[command][0][1:1] = selected["cache"]
            warm_code = (
                "import update_driver.__main__\n"
                "import update_driver._runner\n"
                "import agent_procutil.owned_process\n"
                "import agent_procutil._caller_owned\n"
                + (
                    "import worktree_manager.__main__\n"
                    "import worktree_manager.engine_client\n"
                    if name == "manager" else
                    "import agent_worktrees.__main__ as engine\n"
                    "engine._load_full_command_surface()\n"
                )
            )
            phase(name + "-routing-import-start")
            run([python, "-I", *selected["cache"], "-c", warm_code])
            phase(name + "-routing-import-completed")
            selected.update(
                slot=str(ready.slot), payload_hash=ready.payload_hash,
                origins=json.loads((ready.slot / ".runtime-input-origins.json").read_text(encoding="utf-8")),
            )
            document[name] = selected
        (directory / "prepared-runtime.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        phase("preparation-completed")


def selected_inputs(scope):
    from agent_worktrees import __main__ as engine, update_cli
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
    if sys.argv[1] == "prepare":
        from contextlib import redirect_stderr, redirect_stdout
        import traceback

        prefix = os.environ["UPDATE_DRIVER_TEST_PREPARATION_LOG"]
        with open(prefix + ".stdout.log", "w", encoding="utf-8") as out, open(
            prefix + ".stderr.log", "w", encoding="utf-8",
        ) as err, redirect_stdout(out), redirect_stderr(err):
            try:
                prepare(Path(sys.argv[2]))
            except BaseException:
                traceback.print_exc()
                raise
        return 0
    scope = Path(os.environ["AGENT_HOME"])
    if sys.argv[1] == "manager":
        phase("manager-entry-cwd", cwd=str(Path.cwd()))
        if prepared is None:
            sys.path.insert(0, str(repository / "worktree-manager" / "src"))
        phase("manager-import-start")
        from worktree_manager import __main__ as manager, engine_client
        phase("manager-imported")

        def return_to_engine(rest):
            phase("engine-client-start")
            (scope / "manager-entered").write_text(
                os.environ.get("AGENT_WORKTREES_UPDATE_CONTEXT", ""), encoding="utf-8",
            )
            project, flags = manager._extract_project(rest)
            engine_client.set_engine_command([
                *(prepared["engine"]["script"][0] if prepared else [
                    sys.executable, str(Path(__file__).resolve()),
                ]), "return",
            ])
            result = engine_client.run_engine_passthrough(
                project, ["update", "--no-manager", *flags],
            )
            phase("engine-client-completed")
            return result

        manager._cmd_update = return_to_engine
        phase("manager-main-start")
        return manager.main(sys.argv[2:])
    phase("engine-import-start")
    import update_driver
    from agent_worktrees import __main__ as engine
    from agent_worktrees import front_door_cli, update_cli
    phase("engine-imported")

    front_door_cli.inst.read_projects_registry = lambda: {
        "projects": {"example": {"anchor": str(scope / "anchor")}},
    }
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
        phase("record-result")
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
            *(prepared["manager"]["script"][0] if prepared else [
                sys.executable, str(Path(__file__).resolve()),
            ]), "manager",
        ]
        engine._usable_worktree_manager = update_cli._usable_worktree_manager

        def handoff(args):
            phase("manager-handoff-start")
            handed_off = update_cli.manager_handoff(args)
            return handed_off if handed_off is not None else canonical(args)

        update_cli.cmd_update = handoff
    phase("engine-main-start")
    return engine.main(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
