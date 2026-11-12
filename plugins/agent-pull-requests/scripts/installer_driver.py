"""Fixed native plugin installation under the existing execution driver."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
NATIVE_ENV = "COPILOT_EXTENSIONS_NATIVE_INSTALLER"
SOURCE_SELECTION_ENV = "COPILOT_EXTENSIONS_NATIVE_INSTALL_SOURCE_SELECTION"
WHEEL_SELECTION_ENV = "COPILOT_EXTENSIONS_NATIVE_WHEEL_SELECTION"
WHEEL_FIELDS = (
    ("wheel_profile", "-WheelProfile"), ("wheel_directory", "-WheelDirectory"),
    ("wheel_python", "-WheelPython"), ("wheel_uv", "-WheelUv"),
)
RECIPES = {
    "agent-bridge": frozenset({"Repair", "RequireGracefulCutover", "NonInteractive", "ZeroDowntime", "Purge", "DryRun"}),
    "agent-codespaces": frozenset({"Force", "DryRun", "Repair"}),
    "agent-dispatch": frozenset({
        "NoService", "NoSupervisor", "Interactive", "Purge", "Force",
        "RequireGracefulCutover", "PreserveSupervisors", "ZeroDowntime", "DryRun", "Repair",
    }),
    "agent-pull-requests": frozenset({"Force", "Repair"}),
    "agent-vault": frozenset({"NoService", "Purge", "Force", "DryRun", "Repair"}),
    "agent-mcp": frozenset({"Force", "Repair", "Serve"}),
    "agent-index": frozenset({
        "NoService", "Purge", "Force", "ZeroDowntime", "AllowTaskElevation", "DryRun", "Repair",
    }),
    "agent-containers": frozenset({"Force", "Repair"}),
    "agent-machines": frozenset({"Force", "Repair"}),
    "agent-logger": frozenset({"Repair"}),
    "agent-ssh": frozenset({"Force", "Repair"}),
    "budget-guidance": frozenset({"Force", "Repair"}),
}
ENTRYPOINTS = dict.fromkeys(
    ("agent-mcp", "agent-containers", "agent-machines"), "init.ps1",
)
DEFAULT_ACTIONS = frozenset({"install", "update", "provision", "uninstall"})
ACTIONS = {
    "agent-mcp": frozenset({"install", "init", "provision", "broker-remove"}),
    "agent-containers": frozenset({"install", "init", "provision"}),
    "agent-machines": frozenset({"install", "init", "provision"}),
    "agent-dispatch": DEFAULT_ACTIONS | {"restore"},
}
CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", "COPILOT_PROJECT_DIR",
    "AGENT_BRIDGE_INSTALL_DIR", "AGENT_BRIDGE_CONFIG_DIR", "AGENT_BRIDGE_NONINTERACTIVE",
    "AGENT_BRIDGE_INSTALL_DEADLINE_SEC", "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC",
    "AGENT_CODESPACES_INSTALL_DIR", "AGENT_CODESPACES_HOME",
    "AGENT_CONTAINERS_HOME", "AGENT_CONTAINERS_INSTALL_PROFILE",
    "AGENT_LOGGER_HOME", "AGENT_LOGGER_REPO_CONFIG", "AGENT_WORKTREES_REPOS_YAML",
    "AGENT_MACHINES_HOME", "AGENT_PULL_REQUESTS_HOME", "AGENT_SSH_HOME",
    "BUDGET_GUIDANCE_HOME",
    "AGENT_DISPATCH_ALLOW_DOWNGRADE", "AGENT_DISPATCH_INSTALL_DEADLINE_SEC",
    "AGENT_DISPATCH_BASE_PYTHON", "AGENT_DISPATCH_RUN_DIR", "AGENT_DISPATCH_ROUTING_DIR",
    "AGENT_DISPATCH_INSTALLATION_CONFIG",
    "AGENT_DISPATCH_SUPERVISE_MACHINE", "AGENT_PULL_REQUESTS_INSTALL_DEADLINE_SEC",
    "AGENT_VAULT_HOME", "AGENT_VAULT_INSTALLATION_ID", "AGENT_VAULT_ALLOW_DOWNGRADE",
    "AGENT_VAULT_CONFIG", "AGENT_VAULT_INSTALL_PROFILE",
    "APPDATA", "XDG_CONFIG_HOME", "AGENT_CONFIGURATION_CONTEXT",
    "AGENT_MCP_HOME", "AGENT_MCP_INSTALL_DEADLINE_SEC",
    "AGENT_MCP_SERVE_SOCKET", "AGENT_MCP_NO_CUTOVER",
    "AGENT_MCP_CUTOVER_HEALTH_TIMEOUT", "AGENT_MCP_CUTOVER_DRAIN_TIMEOUT",
    "AGENT_INDEX_INSTALL_DEADLINE_SEC", "AGENT_INDEX_ALLOW_DOWNGRADE", "AGENT_INDEX_ROLE",
    "AGENT_INDEX_CONFIG", "AGENT_INDEX_REPO", "AGENT_INDEX_MACHINE", "AGENT_INDEX_ENGINE_HOME",
    "AGENT_INDEX_INSTALLATION_CONFIG", "AGENT_INDEX_CONFIG_ROOT",
    "AGENT_INDEX_ENGINE_HOST", "AGENT_INDEX_ENGINE_PORT", "AGENT_INDEX_ALLOW_TASK_ELEVATION",
    "CE_UPDATE_AUTH",
    "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET",
    SOURCE_SELECTION_ENV,
    WHEEL_SELECTION_ENV,
)
TRANSIENT_KEYS = (
    NATIVE_ENV, "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_PLUGIN_INSTALL_STAGED",
    "COPILOT_PLUGIN_STAGED_FROM", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
)
INSTALLATION_SELECTORS = {
    "agent-dispatch": "AGENT_DISPATCH_INSTALLATION_CONFIG",
    "agent-index": "AGENT_INDEX_INSTALLATION_CONFIG",
}
SELECTOR_KEYS = (*INSTALLATION_SELECTORS.values(), "AGENT_INDEX_CONFIG_ROOT")


def selector_context(args, environment, *, cwd: Path | None = None) -> dict[str, str]:
    """Resolve explicit selectors before staging; never replace one with defaults."""
    selected = {}
    base = Path.cwd() if cwd is None else cwd
    for name, value in environment.items():
        if any(name.startswith(key + "_") for key in SELECTOR_KEYS):
            raise ValueError(f"Unsupported native installation selector: {name}")
        if name not in SELECTOR_KEYS:
            continue
        if not isinstance(value, str) or not value.strip() or any(char in value for char in "\0\r\n"):
            raise ValueError(f"Invalid native installation selector: {name}")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        if name == INSTALLATION_SELECTORS.get(args.plugin) or (
            args.plugin == "agent-index" and name == "AGENT_INDEX_CONFIG_ROOT"
        ):
            for member in (path, *path.parents):
                try:
                    info = member.lstat()
                except FileNotFoundError:
                    continue
                if member.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError(f"Linked native installation selector: {name}")
        path = Path(os.path.abspath(path))
        selected[name] = str(path)
    if args.plugin == "agent-index" and "AGENT_INDEX_CONFIG_ROOT" in selected:
        directory = Path(selected["AGENT_INDEX_CONFIG_ROOT"])
        for member in (directory, *directory.parents):
            try:
                info = member.lstat()
            except FileNotFoundError:
                continue
            if member.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked native installation selector: AGENT_INDEX_CONFIG_ROOT")
        if directory.exists() and not directory.is_dir():
            raise ValueError("AGENT_INDEX_CONFIG_ROOT must select a directory")
    name = INSTALLATION_SELECTORS.get(args.plugin)
    if name not in selected:
        return selected
    path = Path(selected[name])
    for member in (path, *path.parents):
        try:
            info = member.lstat()
        except FileNotFoundError:
            continue
        if member.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Linked native installation selector: {name}")
    info = path.stat()
    if not path.is_file() or info.st_size > 65536:
        raise ValueError(f"Native installation selector is not a bounded file: {name}")
    with path.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError(f"Native installation selector exceeds its bound: {name}")
    document = json.loads(content)
    installation = document.get("installation") if isinstance(document, dict) else None
    root = installation.get("root") if isinstance(installation, dict) else None
    if (
        not isinstance(root, str) or not Path(root).is_absolute()
        or os.path.normcase(os.path.abspath(root)) != os.path.normcase(str(args.root.resolve()))
    ):
        raise ValueError(f"Native installation selector belongs to a foreign root: {name}")
    return selected


def bootstrap(payload: Path):
    import installer_engine as engine

    required = {"agent-update-driver", "agent-single-instance-lease", "agent-procutil", "agent-zdd"}
    packages = {
        engine._package_name(package): package for package in engine._package_roots(payload)
        if engine._package_name(package) in required
    }
    if packages.keys() != required:
        raise RuntimeError("Native installation requires its complete execution-driver source closure")
    sys.path[:0] = [str(packages[name] / "src") for name in sorted(required)]
    from update_driver import core

    return core


@contextmanager
def custody():
    from update_driver import core, borrowed_custody, participant, participant_environment
    from update_driver.participant import ENVIRONMENT_KEY, MAX_FRAME, ParticipantError

    inherited = participant_environment()
    transport = os.environ.get(ENVIRONMENT_KEY, inherited.get(ENVIRONMENT_KEY))
    owner = None
    if transport is not None:
        try:
            if not isinstance(transport, str) or len(transport) > MAX_FRAME:
                raise ValueError("invalid native participant frame")
            body = json.loads(transport)
            if not isinstance(body, dict) or not isinstance(body.get("root"), str):
                raise ValueError("missing native participant execution root")
            root = core._root(Path(body["root"]))
        except (TypeError, ValueError) as error:
            raise ParticipantError("malformed native participant handoff") from error
        # Root metadata selects the addressed owner; the existing IPC still authenticates it.
        owner = borrowed_custody(root=root)
        if owner is None:
            owner = participant(root=root, witness=transport)
    if owner is None:
        yield None
    else:
        with owner, owner.execution_scope():
            yield owner


def source_digest(payload: Path) -> str:
    import installer_engine as engine

    return engine.runtime.fingerprint_source(engine._source_files(engine._package_roots(payload)))


def native_arguments(args) -> list[str]:
    if getattr(args, "cell_context", None) is not None:
        return cell_arguments(args)
    if any(getattr(args, name, None) is not None for name, _flag in CELL_FIELDS):
        raise ValueError("Cell installation fields require an explicit cell context")
    options = args.options
    if args.plugin not in RECIPES or args.action not in ACTIONS.get(args.plugin, DEFAULT_ACTIONS):
        raise ValueError("Unsupported native installer action")
    if (
        not isinstance(options, list)
        or any(not isinstance(option, str) or option not in RECIPES[args.plugin] for option in options)
        or len(options) != len(set(options))
    ):
        raise ValueError("Unsupported native installer options")
    arguments = [
        "-Action", args.action, "-InstallDir", str(args.root.resolve()),
        *("-" + option for option in sorted(options)),
    ]
    install_source = getattr(args, "install_source", None)
    wheel_values = [getattr(args, name, None) for name, _flag in WHEEL_FIELDS]
    if any(value is not None for value in wheel_values):
        if (
            install_source is not None or not all(wheel_values)
            or args.action not in {"install", "update", "init", "provision"}
            or any(not Path(value).is_absolute() for value in wheel_values)
            or Path(args.wheel_python).resolve(strict=True) != Path(sys.executable).resolve(strict=True)
        ):
            raise ValueError("Closed wheels require one complete native input and its selected interpreter")
        from install_source import wheel_inputs, wheel_selection

        with wheel_inputs(args.wheel_profile, args.wheel_directory) as inputs:
            selection = json.dumps(
                wheel_selection(args.payload, args.plugin, inputs),
                sort_keys=True, separators=(",", ":"),
            )
        previous = getattr(args, "wheel_selection", None) or os.environ.get(WHEEL_SELECTION_ENV)
        if previous is not None and previous != selection:
            raise ValueError("Native wheel selection changed after driver admission")
        args.wheel_selection = selection
        for name, flag in WHEEL_FIELDS:
            arguments.extend([flag, str(getattr(args, name))])
    elif os.environ.get(WHEEL_SELECTION_ENV):
        raise ValueError("Native wheel selection requires its explicit installation input")
    if "Repair" in options and install_source is None:
        raise ValueError("Native repair requires its explicit installation source")
    if "Serve" in options and install_source is None:
        raise ValueError("Native serving requires its explicit installation source")
    if install_source is not None:
        if (
            args.action not in {"install", "update", "init", "provision"}
            or not install_source.is_absolute()
        ):
            raise ValueError("Unsupported native installation-source operation")
        from install_source import native_selection, project_inputs, sources

        with project_inputs(install_source, args.plugin) as (selected, inputs):
            if selected.source_descriptor is None or sources.read_source_identity(
                args.payload, selected.identity.package_version, plugin=args.plugin,
            ) != selected.identity:
                raise ValueError("Native source installation requires its exact source and repository provenance")
            selection = json.dumps(native_selection(selected, inputs), sort_keys=True, separators=(",", ":"))
        previous = getattr(args, "source_selection", None) or os.environ.get(SOURCE_SELECTION_ENV)
        if previous is not None and previous != selection:
            raise ValueError("Native installation-source selection changed after admission")
        args.source_selection = selection
        arguments.extend(["-InstallSource", str(install_source)])
    elif os.environ.get(SOURCE_SELECTION_ENV):
        raise ValueError("Native source selection requires its explicit installation input")
    record = getattr(args, "retained_runtime", None)
    expected = getattr(args, "retained_runtime_sha256", None)
    if args.action == "restore":
        if not record:
            raise ValueError("Retained restoration requires its exact record")
        record = Path(record).absolute()
        if record.resolve() != record or not record.is_file() or record.stat().st_size > 1048576:
            raise ValueError("Retained restoration record is missing, indirect or over budget")
        with record.open("rb") as stream:
            content = stream.read(1048577)
        if len(content) > 1048576:
            raise ValueError("Retained restoration record is over budget")
        digest = hashlib.sha256(content).hexdigest()
        if expected and expected != digest:
            raise ValueError("Retained restoration record changed after admission")
        args.retained_runtime = record
        args.retained_runtime_sha256 = digest
        arguments.extend(["-RetainedRuntime", str(record), "-RetainedRuntimeSha256", digest])
    elif record or expected:
        raise ValueError("Retained restoration input requires its owning restore action")
    return arguments


CELL_FIELDS = (
    ("cell_context", "--context"), ("cell_marketplace", "--expected-marketplace-id"),
    ("cell_durable_home", "--durable-home"), ("cell_snapshot", "--snapshot-id"),
    ("cell_version", "--runtime-version"),
    ("cell_namespace_generation", "--expected-namespace-generation"),
    ("cell_install_generation", "--expected-install-generation"),
)


def cell_arguments(args) -> list[str]:
    if (
        args.plugin != "agent-pull-requests"
        or args.action not in {
            "install", "update", "provision", "broker-remove",
            "snapshot-prepare", "snapshot-stamp", "slot-provision", "slot-complete",
        }
        or args.options or getattr(args, "retained_runtime", None)
        or any(getattr(args, name, None) is not None for name, _flag in WHEEL_FIELDS)
        or getattr(args, "install_source", None) is not None
        or getattr(args, "retained_runtime_sha256", None)
        or Path(args.cell_context).absolute() != args.root / "install.json"
    ):
        raise ValueError("Unsupported owning cell installation recipe")
    for name in ("cell_namespace_generation", "cell_install_generation"):
        value = getattr(args, name, None)
        if type(value) is not int or value < 1:
            raise ValueError("Cell installation requires exact admitted receipt generations")
    arguments = [args.action]
    for name, flag in CELL_FIELDS:
        value = getattr(args, name, None)
        if value is not None:
            arguments.extend([flag, str(value)])
    return arguments


def request_arguments(args) -> tuple[str, ...]:
    arguments = native_arguments(args)
    if args.action == "restore":
        # The helper pins the same bytes at a private path while holding retention.
        index = arguments.index("-RetainedRuntime")
        del arguments[index:index + 2]
    return tuple(arguments)


def _settle(wait):
    while True:
        try:
            return wait()
        except (KeyboardInterrupt, SystemExit):
            continue


def admit_broker_removal(args, *, controller_custody=None) -> None:
    from installer_engine_uninstall import cleanup

    profile = args.payload / "scripts" / "uninstall-profile.json"
    cleanup(
        args.payload, args.plugin, args.root, str(args.shell),
        profile=profile if profile.is_file() else None, check_consumers=True,
        **({"controller_custody": controller_custody} if args.plugin == "agent-vault" else {}),
    )


def execute(args) -> int:
    from update_driver import core, participant_environment

    with custody() as owner:
        if owner is None:
            raise RuntimeError("Native installer participant requires live driver custody")
        request = core.status(owner.request_id, root=owner.root)
        arguments = native_arguments(args)
        environment = {**os.environ, **participant_environment()}
        selectors = selector_context(args, environment)
        if request.state != "running":
            raise RuntimeError("Native installer has no running execution owner")
        if request.spec.backend == "plugin-native-install":
            if (
                request.spec.argv != request_arguments(args)
                or request.spec.source != source_digest(args.payload)
                or request.spec.project != str(args.root.resolve())
            ):
                raise RuntimeError("Native installation changed after driver admission")
            admitted = {
                key: value for key, value in request.spec.context if key in SELECTOR_KEYS
            }
            if admitted != selectors:
                raise RuntimeError("Native installation selectors changed after driver admission")
        elif request.spec.backend not in {
            "manager", "engine", "engine-stage-update", "engine-reconcile-plugins",
            "engine-launch-update", "owning-plugin-provision",
        }:
            raise RuntimeError("Execution owner does not authorize native plugin installation")
        owner.assert_owned()
        if args.remove_broker:
            if args.action != "uninstall" and not (
                args.plugin == "agent-mcp" and args.action == "broker-remove"
            ):
                raise ValueError("Broker removal requires the owning uninstall action")
            from update_driver.windows_broker import remove

            admit_broker_removal(args, controller_custody=owner)
            owner.assert_owned()
            remove(args.root)
            return 0
        if args.check_native:
            return 0
        environment.update(selectors)
        environment[NATIVE_ENV] = args.plugin
        if getattr(args, "source_selection", None) is not None:
            environment[SOURCE_SELECTION_ENV] = args.source_selection
        if getattr(args, "wheel_selection", None) is not None:
            environment[WHEEL_SELECTION_ENV] = args.wheel_selection
        if getattr(args, "cell_context", None) is not None:
            command = [sys.executable, "-I", "-B", str(args.payload / "scripts" / "cell_install.py"), *arguments]
        else:
            command = [str(args.shell), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File",
             str(args.payload / "scripts" / ENTRYPOINTS.get(args.plugin, "install.ps1")),
             *arguments]
        if getattr(args, "source_selection", None) is not None:
            selection = json.loads(args.source_selection)
            command = [
                sys.executable, "-I", "-B", str(args.payload / "scripts" / "command_binding.py"),
                "--payload-root", str(args.payload), "--source-payload-root", str(args.payload),
                "--plugin-id", args.plugin, "--legacy-root", str(args.root),
                "--project-root", str(Path.cwd().resolve()), "--source-descriptor",
                json.dumps(selection["source_descriptor"], sort_keys=True),
                "--run-lifecycle", "--managed-deployment", "--", *command,
            ]
        process = subprocess.Popen(
            command,
            env=environment, stdout=sys.stdout, stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            code = process.wait()
        except BaseException:
            _settle(process.wait)
            raise
        owner.assert_owned()
        if code:
            print(f"Native {args.plugin} installer failed (exit {code})", file=sys.stderr, flush=True)
        return code


def foreground(args, driver) -> int:
    if any(os.environ.get(key) for key in (
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
    )):
        raise RuntimeError("Native driver admission must precede publication custody")
    native_arguments(args)
    selectors = selector_context(args, os.environ)
    command = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(SCRIPTS / "installer_driver.py"),
        "--payload", str(args.payload), "--root", str(args.root), "--shell", str(args.shell),
        "--plugin", args.plugin, "--action", args.action, "--participant",
    ]
    if getattr(args, "install_source", None) is not None:
        command.extend(["--install-source", str(args.install_source)])
    for name, _flag in WHEEL_FIELDS:
        value = getattr(args, name, None)
        if value is not None:
            command.extend(["--" + name.replace("_", "-"), str(value)])
    for option in args.options:
        command.extend(["--option", option])
    for name, flag in CELL_FIELDS:
        value = getattr(args, name, None)
        if value is not None:
            command.extend(["--" + name.replace("_", "-"), str(value)])
    if args.action == "restore":
        command.extend([
            "--retained-runtime", str(args.retained_runtime),
            "--retained-runtime-sha256", args.retained_runtime_sha256,
        ])
    context = {key: os.environ[key] for key in CONTEXT_KEYS if key in os.environ}
    context.update(selectors)
    if getattr(args, "source_selection", None) is not None:
        context[SOURCE_SELECTION_ENV] = args.source_selection
    if getattr(args, "wheel_selection", None) is not None:
        context[WHEEL_SELECTION_ENV] = args.wheel_selection
    request = driver.enqueue(
        driver.RequestSpec(
            "plugin-native-install", request_arguments(args), project=str(args.root.resolve()),
            cwd=str(Path.cwd().resolve()), source=source_digest(args.payload),
            context=tuple(context.items()),
        ),
        runner=driver.TrustedRunner(
            tuple(command), str(Path.cwd().resolve()), context_keys=(*CONTEXT_KEYS, *TRANSIENT_KEYS),
        ),
    )
    driver.drain()
    result = driver.wait(request.id, output=sys.stdout)
    if result.state != "completed" or result.exit_code is None:
        raise RuntimeError(f"Native plugin installation did not complete: {result.state}")
    return result.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--shell", required=True, type=Path)
    parser.add_argument("--plugin", required=True, choices=tuple(RECIPES))
    parser.add_argument(
        "--action", required=True,
        choices=sorted(DEFAULT_ACTIONS.union(*ACTIONS.values(), {
            "snapshot-prepare", "snapshot-stamp", "slot-provision", "slot-complete",
        })),
    )
    parser.add_argument("--option", dest="options", action="append", default=[])
    parser.add_argument("--retained-runtime", type=Path)
    parser.add_argument("--retained-runtime-sha256")
    parser.add_argument("--install-source", type=Path)
    for name, _flag in WHEEL_FIELDS:
        parser.add_argument("--" + name.replace("_", "-"), type=Path)
    for name, _flag in CELL_FIELDS:
        parser.add_argument(
            "--" + name.replace("_", "-"),
            type=int if name.endswith("_generation") else str,
        )
    parser.add_argument("--participant", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check-native", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remove-broker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Native installer driver entry requires Windows")
    args.payload = args.payload.resolve(strict=True)
    args.root = args.root.resolve()
    args.shell = args.shell.resolve(strict=True)
    if args.payload != SCRIPTS.parent or args.shell.name.lower() not in {"pwsh.exe", "powershell.exe"}:
        parser.error("Native installation requires this executing payload and an exact PowerShell executable")
    import installer_engine as engine

    if engine._package_name(args.payload) != args.plugin:
        parser.error("Native installation does not match its declared plugin")
    native_arguments(args)
    driver = bootstrap(args.payload)
    with custody() as owner:
        if owner is None:
            if args.participant or args.check_native or args.remove_broker:
                raise RuntimeError("Native installer participant requires live driver custody")
            return foreground(args, driver)
        return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
