"""Fixed owning-payload provision requests, not a command submission interface."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs

from . import core
from .participant import ENVIRONMENT_KEY, borrowed_custody, participant_environment


# Fixed owning lifecycle actions only; no caller-supplied installer argv.
RECIPES = {
    "agent-vault": "AGENT_VAULT",
    "agent-pull-requests": "AGENT_PULL_REQUESTS",
    "agent-ssh": "AGENT_SSH",
    "agent-codespaces": "AGENT_CODESPACES",
    "agent-logger": "AGENT_LOGGER",
    "agent-bridge": "AGENT_BRIDGE",
}
LOGGER_COMMANDS = (
    ("agent-logger", "agent_logger"),
    ("collate-session", "agent_logger.segmenter.collate"),
    ("read-session-digest", "agent_logger.segmenter.read_digest"),
    ("prepare-session-log", "agent_logger.segmenter.prepare_log"),
    ("ramp-up-session", "agent_logger.segmenter.ramp_up"),
    ("session-sync", "agent_logger.sync.engine"),
)
PROTOCOL = 1
INSTALLER_ACTIONS = ("provision", "stamp", "stamp-provision", "install")
CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_VAULT_HOME", "AGENT_VAULT_INSTALLATION_ID",
    "AGENT_VAULT_NO_SELFPROVISION", "AGENT_VAULT_PINNED_CONTEXT",
    "AGENT_VAULT_ALLOW_DOWNGRADE",
)
PULL_REQUESTS_CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_PULL_REQUESTS_HOME", "AGENT_PULL_REQUESTS_INSTALLATION_ID",
    "AGENT_PULL_REQUESTS_NO_SELFPROVISION", "AGENT_PULL_REQUESTS_PINNED_CONTEXT",
)
SSH_CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_SSH_HOME", "AGENT_SSH_INSTALLATION_ID",
    "AGENT_SSH_NO_SELFPROVISION", "AGENT_SSH_PINNED_CONTEXT",
)
CODESPACES_CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_CODESPACES_HOME", "AGENT_CODESPACES_INSTALLATION_ID",
    "AGENT_CODESPACES_NO_SELFPROVISION", "AGENT_CODESPACES_PINNED_CONTEXT",
)
LOGGER_CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_LOGGER_HOME", "AGENT_LOGGER_INSTALLATION_ID",
    "AGENT_LOGGER_NO_SELFPROVISION", "AGENT_LOGGER_PINNED_CONTEXT",
)
BRIDGE_CONTEXT_KEYS = (
    "HOME", "USERPROFILE", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_BRIDGE_HOME", "AGENT_BRIDGE_INSTALLATION_ID",
    "AGENT_BRIDGE_NO_SELFPROVISION", "AGENT_BRIDGE_PINNED_CONTEXT",
    "AGENT_BRIDGE_ALLOW_DOWNGRADE", "AGENT_BRIDGE_NONINTERACTIVE",
)
BRIDGE_HELPERS = (
    "installer-engine.ps1", "installer-engine.sh",
    "installer_engine.py", "installer_engine_process.py", "runtime_launcher.py",
    "installer_wheel_inputs.py", "installer_wheel_snapshot.py", "installer_wheel_acquisition.py",
    "installer_dependency_policy.py", "installer_dependency_config.py",
    "installation-context/installation_context.py",
    "installation-context/installation-context.ps1", "installation-context/installation-context.sh",
    "install.ps1", "install.sh", "repair-scheduled-task.ps1",
    "versioned_runtime_native.ps1", "versioned_runtime.py",
    "versioned_runtime_construction.py", "versioned_runtime_source.py",
    "versioned_runtime_publication.py", "versioned_runtime_leases.py",
)


class ProvisionError(core.DriverError):
    """Owning-source or publication admission refused provisioning."""


def _path(path: Path, *, exists: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ProvisionError("provision paths must be absolute and unlinked")
    try:
        resolved = path.resolve()
    except RuntimeError as error:
        raise ProvisionError(f"provision path resolution failed: {error}") from error
    if resolved != path:
        raise ProvisionError("provision paths must be absolute and unlinked")
    if exists and not path.is_dir():
        raise ProvisionError("provision source or project directory is unavailable")
    return path


def _environment(payload: Path) -> dict[str, str]:
    result = dict(os.environ)
    result["COPILOT_PLUGIN_ROOT"] = str(payload)
    # An unrelated ambient payload cannot replace the explicitly owning source.
    for key in (
        "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_PROJECT_DIR", "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "UPDATE_DRIVER_WITNESS",
        "AGENT_VAULT_VALIDATED_CONTEXT", "AGENT_PULL_REQUESTS_VALIDATED_CONTEXT",
        "AGENT_PULL_REQUESTS_ACTION", "AGENT_PULL_REQUESTS_FORCE",
        "AGENT_PULL_REQUESTS_INSTALL_DIR",
        "AGENT_SSH_VALIDATED_CONTEXT", "AGENT_SSH_PAYLOAD_ROOT",
        "AGENT_SSH_ACTION", "AGENT_SSH_DRY_RUN",
        "AGENT_CODESPACES_VALIDATED_CONTEXT", "AGENT_CODESPACES_PAYLOAD_ROOT",
        "AGENT_CODESPACES_ACTION", "AGENT_CODESPACES_FORCE", "AGENT_CODESPACES_DRY_RUN",
        "AGENT_LOGGER_VALIDATED_CONTEXT", "AGENT_LOGGER_PAYLOAD_ROOT",
        "AGENT_LOGGER_ACTION", "AGENT_LOGGER_DRY_RUN",
        "AGENT_BRIDGE_VALIDATED_CONTEXT", "AGENT_BRIDGE_PAYLOAD_ROOT",
        "AGENT_BRIDGE_ACTION", "AGENT_BRIDGE_DRY_RUN", "AGENT_BRIDGE_FORCE",
        "AGENT_BRIDGE_CONFIG_DIR", "AGENT_BRIDGE_INSTALL_DIR",
    ):
        result.pop(key, None)
    return result


def _inspect(
    payload: Path, runtime_root: Path, project: Path, *, installer_action: str = "provision",
) -> dict:
    """Run only in the isolated inspection/owned execution process."""
    sys.dont_write_bytecode = True
    if installer_action not in INSTALLER_ACTIONS:
        raise ProvisionError("unsupported owning installer action")
    payload = _path(payload, exists=True)
    runtime_root = _path(runtime_root)
    project = _path(project, exists=True)
    scripts = payload / "scripts"
    helper = scripts / "installation-context" / "installation_context.py"
    if not helper.is_file() or not (scripts / "installer_engine.py").is_file():
        raise ProvisionError("owning payload requires complete materialized installation helpers")
    manifest = json.loads((payload / "payload-invocation.json").read_text(encoding="utf-8"))
    identity = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(identity, dict):
        raise ProvisionError("owning payload requires object-shaped installation declarations")
    plugin = identity.get("name")
    if not isinstance(plugin, str) or plugin not in RECIPES:
        raise ProvisionError("owning payload does not declare a supported fixed provision recipe")
    if installer_action == "stamp-provision" and plugin != "agent-ssh":
        raise ProvisionError("stamp-to-selected-snapshot provisioning requires SSH")
    if installer_action == "install" and plugin not in ("agent-logger", "agent-bridge"):
        raise ProvisionError("fixed install action requires Logger or Bridge")
    prefix = RECIPES[plugin]
    if plugin == "agent-bridge" and any(not (scripts / name).is_file() for name in BRIDGE_HELPERS):
        raise ProvisionError("Bridge first use requires its complete materialized installation helpers")
    if plugin == "agent-bridge" and sys.platform.startswith("linux"):
        for tool in ("xargs", "seq"):
            if shutil.which(tool) is None:
                raise ProvisionError(f"Bridge first use requires native tool: {tool}")
    no_self = prefix + "_NO_SELFPROVISION"
    if (
        manifest.get("schema") != "copilot-extensions.payload-invocation"
        or type(manifest.get("version")) is not int or manifest["version"] != 2
        or (plugin != "agent-logger" and manifest.get("command") != plugin)
        or manifest.get("installationContext") != "required"
        or (plugin != "agent-logger" and manifest.get("provisionMode", "snapshot") != (
            "snapshot" if plugin in ("agent-ssh", "agent-codespaces") else "direct"
        ))
        or manifest.get("installer", "install") != "install"
        or manifest.get("noSelfProvisionEnv") != no_self
    ):
        raise ProvisionError("owning payload does not declare the fixed provision recipe")
    if plugin == "agent-logger":
        if any(not (scripts / name).is_file() for name in ("command_binding.py", "command_lifecycle.py")):
            raise ProvisionError("Logger first use requires its canonical lifecycle helpers")
        commands = manifest.get("commands")
        if (
            manifest.get("plugin") != plugin or manifest.get("installerLifecycle") is not True
            or any(key in manifest for key in ("command", "module", "provisionMode"))
            or not isinstance(commands, list)
            or any(not isinstance(item, dict) for item in commands)
            or tuple((item.get("command"), item.get("module")) for item in commands) != LOGGER_COMMANDS
        ):
            raise ProvisionError("Logger first use requires its fixed six-command lifecycle declaration")
    if os.environ.get(no_self) or (plugin == "agent-pull-requests" and no_self in os.environ):
        raise ProvisionError("owning plugin self-provisioning is disabled")
    if prefix + "_PINNED_CONTEXT" in os.environ:
        raise ProvisionError("pinned identity reads require a prepared owning runtime")
    if plugin in ("agent-ssh", "agent-codespaces", "agent-logger", "agent-bridge"):
        label = {"agent-ssh": "SSH", "agent-codespaces": "CodeSpaces", "agent-logger": "Logger",
                 "agent-bridge": "Bridge"}[plugin]
        if (
            (plugin != "agent-logger" and manifest.get("module") != plugin.replace("-", "_"))
            or manifest.get("legacyRuntimeRoot") != "." + plugin
            or manifest.get("payloadRootEnv") != prefix + "_PAYLOAD_ROOT"
            or manifest.get("payloadDispatcher") != {
                "posix": "scripts/runtime-gate.sh", "windows": "scripts/runtime-gate.ps1",
            }
        ):
            raise ProvisionError(f"{label} first use requires its declared native runtime gate")
    if plugin in ("agent-codespaces", "agent-logger", "agent-bridge") and (
        prefix + "_INSTALLATION_ID" in os.environ
        or runtime_root != Path.home() / ("." + plugin)
        or not (
            payload.parent.parent == Path.home() / ".copilot" / "installed-plugins"
            and payload.name == plugin
            or plugin == "agent-logger" and installer_action == "provision"
            and payload.parent == runtime_root / "snapshots"
        )
    ):
        raise ProvisionError(
            f"{label} first use requires its canonical installed payload and default legacy root"
        )
    if plugin in ("agent-pull-requests", "agent-ssh", "agent-logger"):
        label = {"agent-ssh": "SSH", "agent-logger": "Logger",
                 "agent-pull-requests": "Pull Requests"}[plugin]
        installed = (
            payload.parent.parent == Path.home() / ".copilot" / "installed-plugins"
            and (plugin == "agent-pull-requests" or payload.name == plugin)
        )
        snapshot = payload.parent == runtime_root / "snapshots"
        if runtime_root != Path.home() / ("." + plugin) or not (installed or snapshot):
            raise ProvisionError(
                f"{label} first use requires its canonical installed payload or selected legacy snapshot"
            )
        if snapshot:
            receipt_path = _path(payload / "snapshot-provenance.json")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or receipt.get("schema") != (
                "copilot-extensions.legacy-snapshot-source"
            ):
                raise ProvisionError(f"{label} first use requires canonical selected snapshot provenance")

    sys.path.insert(0, str(scripts))
    import installer_engine as engine

    try:
        roots = engine._package_roots(payload)
    except RuntimeError as error:
        raise ProvisionError(f"owning source dependencies refused: {error}") from error
    if any(not root.is_relative_to(payload) for root in roots):
        raise ProvisionError("first-use provisioning requires a self-contained owning payload")
    digest = engine.runtime.fingerprint_source(engine._source_files(roots))
    spec = importlib.util.spec_from_file_location("_first_use_installation_context", helper)
    if spec is None or spec.loader is None:
        raise ProvisionError("owning installation helper is unavailable")
    primitive = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = primitive
    spec.loader.exec_module(primitive)
    environment = _environment(payload)
    arguments = dict(
        payload_root=payload, plugin_id=plugin, legacy_root=runtime_root,
        project_root=project, environment=environment,
    )
    source = primitive.resolve_payload_source(
        **{key: value for key, value in arguments.items() if key != "legacy_root"},
    )
    resolution = primitive.resolve_runtime_deployment(**arguments)
    selected_root = resolution["runtimeRoot"] or resolution["legacy"]["root"]
    if (
        resolution["status"] != "ready"
        or resolution["actualMode"] != "legacy"
        or Path(selected_root) != runtime_root
        or resolution["deployment"]["allowPluginPublication"] is not True
    ):
        raise ProvisionError("owning installation does not permit plugin publication")
    if engine.runtime.fingerprint_source(engine._source_files(roots)) != digest:
        raise ProvisionError("owning source changed during provision admission")
    return {
        "schema": "copilot-extensions.owning-plugin-provision", "version": PROTOCOL,
        "plugin": plugin, "payload": str(payload), "runtime_root": str(runtime_root),
        "project": str(project), "input_hash": digest,
        "source": source["source"], "source_fingerprint": source["sourceFingerprint"],
        "marketplace_id": source["marketplaceId"],
        "mode": resolution["actualMode"], "environment": resolution["environment"],
        "context": resolution["context"],
        "install_generation": resolution["installGeneration"],
        **({"installer_action": installer_action} if installer_action != "provision" else {}),
    }


def inspect(
    payload: Path, runtime_root: Path, project: Path, *, installer_action: str = "provision",
) -> dict:
    """Read canonical owning-source admission without mutating either installation."""
    if installer_action not in INSTALLER_ACTIONS:
        raise ProvisionError("unsupported owning installer action")
    payload, runtime_root, project = (
        _path(payload, exists=True), _path(runtime_root), _path(project, exists=True),
    )
    argv, imports = core.runtime_command(
        "update_driver.first_use", [
            "_inspect", "--payload", str(payload), "--runtime-root", str(runtime_root),
            "--project", str(project), "--installer-action", installer_action,
        ],
    )
    result = subprocess.run(
        argv, env={**_environment(payload), **imports}, cwd=project,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        timeout=60, **no_window_kwargs(),
    )
    if result.returncode != 0:
        raise ProvisionError("owning source/publication admission failed:\n" + result.stderr)
    return json.loads(result.stdout)


def enqueue(
    payload: Path, runtime_root: Path, project: Path, *, root: Path | None = None,
    expected_source: str | None = None, client_only: bool = False,
    installer_action: str = "provision",
) -> core.Request:
    """Accept only the fixed owning lifecycle actions into the existing queue."""
    if ENVIRONMENT_KEY in os.environ or participant_environment():
        raise ProvisionError("nested first-use submission is unsupported; retain the current installation owner")
    if type(client_only) is not bool:
        raise ProvisionError("client-only selection must be a boolean")
    if installer_action not in INSTALLER_ACTIONS:
        raise ProvisionError("unsupported owning installer action")
    if client_only and installer_action != "provision":
        raise ProvisionError("client-only selection requires the provision action")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        raise ProvisionError("native plugin provision execution is not qualified on this platform")
    execution_root = core._root(root)
    for installation in (Path(payload), Path(runtime_root)):
        if execution_root.is_relative_to(installation) or installation.is_relative_to(execution_root):
            raise ProvisionError("execution root must be separate from owning payload and installation")
    admission = inspect(payload, runtime_root, project, installer_action=installer_action)
    if client_only and admission["plugin"] != "agent-vault":
        raise ProvisionError("client-only selection is unsupported by the owning installer")
    if expected_source is not None and admission["source_fingerprint"] != expected_source:
        raise ProvisionError("owning source differs from native first-use admission")
    encoded = json.dumps(admission, sort_keys=True, separators=(",", ":"))
    argv, imports = core.runtime_command(
        "update_driver.first_use", [
            "_execute", "--root", str(execution_root), "--admission", encoded,
            *(("--client-only",) if client_only else ()),
        ],
    )
    context_keys = {
        "agent-vault": CONTEXT_KEYS,
        "agent-pull-requests": PULL_REQUESTS_CONTEXT_KEYS,
        "agent-ssh": SSH_CONTEXT_KEYS,
        "agent-codespaces": CODESPACES_CONTEXT_KEYS,
        "agent-logger": LOGGER_CONTEXT_KEYS,
        "agent-bridge": BRIDGE_CONTEXT_KEYS,
    }[admission["plugin"]]
    context = {key: os.environ[key] for key in context_keys if key in os.environ}
    context["COPILOT_PLUGIN_ROOT"] = str(payload)
    return core.enqueue(
        core.RequestSpec(
            "owning-plugin-provision",
            argv=(
                ("--installer-action", installer_action) if installer_action != "provision" else
                ("--client-only",) if client_only else ()
            ),
            source=encoded, cwd=str(project),
            context=tuple(context.items()),
        ),
        runner=core.TrustedRunner(
            argv, str(project), env=tuple(imports.items()), context_keys=context_keys,
        ),
        root=root, coalesce_running=True,
    )


def _execute(admission: dict, *, root: Path, client_only: bool = False) -> int:
    custody = borrowed_custody(root=root)
    if custody is None:
        raise ProvisionError("plugin provisioning requires live installation custody")
    with custody, custody.execution_scope():
        payload = Path(admission["payload"])
        runtime_root = Path(admission["runtime_root"])
        installer_action = admission.get("installer_action", "provision")
        logger_snapshot = admission["plugin"] == "agent-logger" and payload.parent == runtime_root / "snapshots"
        if client_only and (
            installer_action != "provision" or admission["plugin"] != "agent-vault"
        ):
            raise ProvisionError("client-only selection requires the Vault provision action")
        try:
            current = _inspect(
                payload, runtime_root, Path(admission["project"]), installer_action=installer_action,
            )
            if current != admission:
                raise ProvisionError("owning provision source or installation changed after acceptance")
            custody.assert_owned()
            import installer_engine as engine

            # Reuse the canonical complete-input copier under this request's custody.
            # Keep these files: native publication may retain a source path into them.
            legacy_stage = admission["plugin"] in {
                "agent-pull-requests", "agent-ssh", "agent-codespaces", "agent-logger",
                "agent-bridge",
            }
            inputs = _path(
                runtime_root / ".install-stage" if legacy_stage
                else root / "provision-inputs"
            )
            inputs.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination = inputs / custody.request_id
            if legacy_stage:
                # Keep legacy staging reapers off this live, queue-owned native tree.
                destination = inputs / f"{custody.request_id}-{os.getpid()}" / admission["plugin"]
            try:
                frozen = engine.freeze_inputs(payload, destination)
            except RuntimeError as error:
                if not logger_snapshot:
                    raise
                raise ProvisionError(f"selected Logger snapshot freeze validation failed: {error}") from error
            if frozen.payload_hash != admission["input_hash"] or _inspect(
                payload, runtime_root, Path(admission["project"]), installer_action=installer_action,
            ) != admission:
                raise ProvisionError("owning source or installation changed while freezing provision inputs")
        except (ProvisionError, OSError, ValueError, ImportError, SyntaxError) as error:
            if not logger_snapshot:
                raise
            # No installer has started; this new recipe can certify a settled refusal.
            custody.assert_owned()
            print(f"Selected Logger source refused before installer execution: {error}", file=sys.stderr)
            return 1
        custody.assert_owned()
        environment = _environment(payload)
        # This trusted adapter, not ambient caller hints, attests the copied
        # source after queue authentication and full before/after verification.
        environment["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
        environment["COPILOT_PLUGIN_STAGED_FROM"] = str(payload)
        # Native installers can discover the executing retained interpreter even
        # when the original first-use caller had no Python on PATH.
        environment["PATH"] = os.pathsep.join(filter(None, (
            str(Path(sys.executable).parent), environment.get("PATH"),
        )))
        code = _run_installer(
            frozen.plugin, runtime_root, admission["project"], environment,
            "stamp" if installer_action == "stamp-provision" else installer_action,
            plugin_id=admission["plugin"], client_only=client_only,
        )
        if code != 0 or installer_action != "stamp-provision":
            return code
        custody.assert_owned()
        snapshot = _path(Path((runtime_root / "payload-dir").read_text(
            encoding="utf-8-sig",
        ).strip()), exists=True)
        selected = _inspect(snapshot, runtime_root, Path(admission["project"]))
        if snapshot.parent != runtime_root / "snapshots" or any(
            selected[key] != admission[key]
            for key in ("plugin", "source", "source_fingerprint", "marketplace_id")
        ) or _ssh_stamp_hash(frozen.plugin) != _ssh_stamp_hash(snapshot):
            raise ProvisionError("stamped snapshot differs from the accepted owning source")
        retained = engine.freeze_inputs(snapshot, destination.parent / "selected" / admission["plugin"])
        if retained.payload_hash != selected["input_hash"] or _inspect(
            snapshot, runtime_root, Path(admission["project"]),
        ) != selected:
            raise ProvisionError("selected snapshot changed while freezing provision inputs")
        custody.assert_owned()
        environment["COPILOT_PLUGIN_ROOT"] = str(snapshot)
        environment["COPILOT_PLUGIN_STAGED_FROM"] = str(snapshot)
        return _run_installer(
            retained.plugin, runtime_root, admission["project"], environment, "provision",
            plugin_id=admission["plugin"],
        )


def _ssh_stamp_hash(payload: Path) -> str:
    import installer_engine as engine

    # The canonical legacy stamp changes only these publication sidecars and
    # intentionally omits top-level tests; every other admitted byte must match.
    sidecars = {"snapshot-provenance.json", ".source-payload-path", ".snapshot-version"}
    files = tuple(
        path for path in engine._source_files(engine._package_roots(payload))
        if path.relative_to(payload).as_posix() not in sidecars
        and path.relative_to(payload).parts[0] != "tests"
    )
    return engine.runtime.fingerprint_source(files)


def _run_installer(
    payload: Path, runtime_root: Path, project: str, environment: dict[str, str],
    installer_action: str, *, plugin_id: str, client_only: bool = False,
) -> int:
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / (
            "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        argv = [
            str(shell), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(payload / "scripts" / "install.ps1"),
            installer_action, "-InstallDir", str(runtime_root),
            *(("-NoService",) if client_only else ()),
        ]
    elif sys.platform.startswith("linux"):
        shell = shutil.which("bash")
        if shell is None:
            raise ProvisionError("native provision shell is unavailable")
        argv = [
            shell, str(payload / "scripts" / "install.sh"),
            installer_action, "--install-dir", str(runtime_root),
            *(("--no-service",) if client_only else ()),
        ]
    else:
        raise ProvisionError("native plugin provision execution is not qualified on this platform")
    if plugin_id == "agent-logger":
        argv = [
            sys.executable, "-I", "-B", "-X", "utf8", str(payload / "scripts/command_binding.py"),
            "--payload-root", str(payload), "--plugin-id", "agent-logger",
            "--legacy-root", str(runtime_root),
            "--source-payload-root", environment["COPILOT_PLUGIN_STAGED_FROM"],
            "--project-root", project,
            *(("--deferred-runtime",) if installer_action == "stamp" else ()),
            "--run-lifecycle", "--", *argv,
        ]
    # The queue's existing owned process encloses this entire native tree.
    # The installer retains its staging, lifecycle and publication checks.
    return subprocess.call(
        argv, env=environment, cwd=project,
        stdin=subprocess.DEVNULL, **no_window_kwargs(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("submit", "_inspect"):
        command = commands.add_parser(action)
        command.add_argument("--payload", type=Path, required=True)
        command.add_argument("--runtime-root", type=Path, required=True)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--installer-action", choices=INSTALLER_ACTIONS, default="provision")
        if action == "submit":
            command.add_argument("--root", type=Path)
            command.add_argument("--expected-source")
            command.add_argument("--client-only", action="store_true")
            command.add_argument("--wait", action="store_true")
    execute = commands.add_parser("_execute")
    execute.add_argument("--root", type=Path, required=True)
    execute.add_argument("--admission", required=True)
    execute.add_argument("--client-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.action == "_inspect":
            print(json.dumps(_inspect(
                arguments.payload, arguments.runtime_root, arguments.project,
                installer_action=arguments.installer_action,
            ), sort_keys=True))
            return 0
        if arguments.action == "_execute":
            return _execute(
                json.loads(arguments.admission), root=arguments.root,
                client_only=arguments.client_only,
            )
        request = enqueue(
            arguments.payload, arguments.runtime_root, arguments.project, root=arguments.root,
            expected_source=arguments.expected_source,
            client_only=arguments.client_only,
            installer_action=arguments.installer_action,
        )
        print(json.dumps({"request_id": request.id, "state": request.state}), flush=True)
        worker_argv, worker_env = core.runtime_command(
            "update_driver", ["worker", "--root", str(core._root(arguments.root))],
        )
        worker = core.ensure_worker(worker_argv, root=arguments.root, env=worker_env)
        if not arguments.wait:
            return 0
        result = core.wait(
            request.id, root=arguments.root, worker=worker, output=sys.stderr,
        )
        if result.state != "completed":
            raise ProvisionError("plugin provision request requires explicit recovery")
        return result.exit_code
    except (core.DriverError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Owning plugin provision refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
