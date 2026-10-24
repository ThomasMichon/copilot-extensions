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
}
PROTOCOL = 1
INSTALLER_ACTIONS = ("provision", "stamp")
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


class ProvisionError(core.DriverError):
    """Owning-source or publication admission refused provisioning."""


def _path(path: Path, *, exists: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
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
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "UPDATE_DRIVER_WITNESS",
        "AGENT_VAULT_VALIDATED_CONTEXT", "AGENT_PULL_REQUESTS_VALIDATED_CONTEXT",
        "AGENT_PULL_REQUESTS_ACTION", "AGENT_PULL_REQUESTS_FORCE",
        "AGENT_PULL_REQUESTS_INSTALL_DIR",
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
    plugin = json.loads((payload / "plugin.json").read_text(encoding="utf-8")).get("name")
    if not isinstance(plugin, str) or plugin not in RECIPES:
        raise ProvisionError("owning payload does not declare a supported fixed provision recipe")
    prefix = RECIPES[plugin]
    no_self = prefix + "_NO_SELFPROVISION"
    if (
        manifest.get("schema") != "copilot-extensions.payload-invocation"
        or type(manifest.get("version")) is not int or manifest["version"] != 2
        or manifest.get("command") != plugin
        or manifest.get("installationContext") != "required"
        or manifest.get("provisionMode") != "direct"
        or manifest.get("installer", "install") != "install"
        or manifest.get("noSelfProvisionEnv") != no_self
    ):
        raise ProvisionError("owning payload does not declare the fixed provision recipe")
    if os.environ.get(no_self) or (plugin == "agent-pull-requests" and no_self in os.environ):
        raise ProvisionError("owning plugin self-provisioning is disabled")
    if prefix + "_PINNED_CONTEXT" in os.environ:
        raise ProvisionError("pinned identity reads require a prepared owning runtime")
    if plugin == "agent-pull-requests" and (
        payload.parent.parent != Path.home() / ".copilot" / "installed-plugins"
        or runtime_root != Path.home() / ".agent-pull-requests"
    ):
        raise ProvisionError("Pull Requests first use requires its canonical installed payload and legacy root")

    sys.path.insert(0, str(scripts))
    import installer_engine as engine

    roots = engine._package_roots(payload)
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
    context_keys = (
        CONTEXT_KEYS if admission["plugin"] == "agent-vault" else PULL_REQUESTS_CONTEXT_KEYS
    )
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
        if client_only and (
            installer_action != "provision" or admission["plugin"] != "agent-vault"
        ):
            raise ProvisionError("client-only selection requires the Vault provision action")
        current = _inspect(
            payload, runtime_root, Path(admission["project"]), installer_action=installer_action,
        )
        if current != admission:
            raise ProvisionError("owning provision source or installation changed after acceptance")
        custody.assert_owned()
        import installer_engine as engine

        # Reuse the canonical complete-input copier under this request's custody.
        # Keep these files: native publication may retain a source path into them.
        inputs = _path(
            runtime_root / ".install-stage" if admission["plugin"] == "agent-pull-requests"
            else root / "provision-inputs"
        )
        inputs.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = inputs / custody.request_id
        if admission["plugin"] == "agent-pull-requests":
            # Keep legacy staging reapers off this live, queue-owned native tree.
            destination = inputs / f"{custody.request_id}-{os.getpid()}" / payload.name
        frozen = engine.freeze_inputs(payload, destination)
        if frozen.payload_hash != admission["input_hash"] or _inspect(
            payload, runtime_root, Path(admission["project"]), installer_action=installer_action,
        ) != admission:
            raise ProvisionError("owning source or installation changed while freezing provision inputs")
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
        if os.name == "nt":
            shell = Path(os.environ["SystemRoot"]) / (
                "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            argv = [
                str(shell), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(frozen.plugin / "scripts" / "install.ps1"),
                installer_action, "-InstallDir", str(runtime_root),
                *(("-NoService",) if client_only else ()),
            ]
        elif sys.platform.startswith("linux"):
            shell = shutil.which("bash")
            if shell is None:
                raise ProvisionError("native provision shell is unavailable")
            argv = [
                shell, str(frozen.plugin / "scripts" / "install.sh"),
                installer_action, "--install-dir", str(runtime_root),
                *(("--no-service",) if client_only else ()),
            ]
        else:
            raise ProvisionError("native plugin provision execution is not qualified on this platform")
        # The queue's existing owned process encloses this entire native tree.
        # The installer retains its staging, lifecycle and publication checks.
        return subprocess.call(
            argv, env=environment, cwd=admission["project"],
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
