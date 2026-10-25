"""Collect combined runtime installer commands without executing them.

This is the explicit update path, not the pull-free session-launch planner or
a prepare/activate adapter. Installer ownership and execution stay unchanged.
Bridge's installer also reconciles serving state when completed content is
current; the outer updater must not bypass that phase.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from collections.abc import Collection, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from . import reconcile

if TYPE_CHECKING:
    from .update_runtime import _RegisteredPluginTarget
    from .update_runtime_source import PreparedRuntimeSource
    from .update_runtime_target import SourceRuntimeTarget


@dataclasses.dataclass(frozen=True)
class RuntimeUpdateStep:
    name: str
    phase: str
    payload: Path
    status: str = ""
    required: bool = True
    eligible: bool = False
    environment: Mapping[str, str] = dataclasses.field(default_factory=dict)
    runtime_root: Path | None = None
    target_version: str | None = None
    argv: tuple[str, ...] = ()
    fallback_argv: tuple[str, ...] = ()
    deadline_s: int = 0
    source: PreparedRuntimeSource | None = None
    source_target: SourceRuntimeTarget | None = None
    project_root: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))

    @property
    def blocked(self) -> bool:
        return bool(self.status) and not (
            self.status.startswith("SKIPPED") or self.status == "payload-only"
        )

    @property
    def artifact(self) -> Path:
        return self.source.payload if self.source is not None else self.payload


def _select_inputs(
    step: RuntimeUpdateStep, source: PreparedRuntimeSource | None,
    project: Path | None = None,
) -> RuntimeUpdateStep:
    try:
        environment, root = reconcile.runtime_installer_environment(step.name, step.payload)
        environment = dict(environment)
        source_target = None
        if source is not None:
            from . import update_runtime as runtime
            from .update_runtime_source import PIN_ENV
            from .update_runtime_target import TARGET_ENV, plan_target

            environment.pop(PIN_ENV, None)
            environment.pop(TARGET_ENV, None)
            scope = reconcile.manifest_runtime_scope(source.payload)
            if scope not in {"universal", "machine-gated"}:
                raise ValueError("Prepared payload does not declare an installable runtime scope")
            try:
                if runtime._machine_gate_blocks(step.name, scope, project):
                    return dataclasses.replace(
                        step, status=runtime.MACHINE_GATED_SKIP, eligible=False,
                    )
            except runtime.MachineDetectionError as error:
                return dataclasses.replace(step, status=f"machine detection failed: {error}")
            source.validate(step.name, step.payload, environment)
            source_target = plan_target(source, step.name, step.payload, root, environment)
            if source_target is not None:
                environment[TARGET_ENV] = source_target.document
            project = (project or Path.cwd()).resolve()
            environment.update({
                PIN_ENV: source.identity,
                "COPILOT_PLUGIN_STAGED_FROM": str(step.payload.resolve()),
                "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(project),
            })
        return dataclasses.replace(
            step, source=source, environment=environment, runtime_root=root,
            source_target=source_target,
            target_version=(
                source_target.version if source_target else
                source.version if source else reconcile.payload_version(step.payload)
            ),
            project_root=project,
        )
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
        label = "installation inputs invalid" if source else "installation context invalid"
        return dataclasses.replace(step, status=f"{label}: {error}")


def _deployed_version(step: RuntimeUpdateStep) -> str | None:
    if step.source is not None:
        assert step.runtime_root is not None
        return step.source.deployed_version(
            step.runtime_root, step.payload, target=step.source_target,
        )
    return reconcile.runtime_deployed_version(step.name, root=step.runtime_root)


def plan_registered(
    name: str, platform_name: str, *, force: bool, gate_context: Path | None = None,
    source: PreparedRuntimeSource | None = None,
) -> RuntimeUpdateStep:
    from . import update_runtime as runtime

    pdir = reconcile.core_installed_payload_dir(name)
    step = RuntimeUpdateStep(name, "runtime_reconcile", pdir or Path())
    if pdir is None:
        return dataclasses.replace(step, status="payload not installed")
    scope = reconcile.manifest_runtime_scope(pdir) or "none"
    if scope == "none":
        return dataclasses.replace(step, status="payload-only")
    step = dataclasses.replace(step, eligible=True)
    try:
        if runtime._machine_gate_blocks(name, scope, gate_context):
            return dataclasses.replace(step, status=runtime.MACHINE_GATED_SKIP, eligible=False)
    except runtime.MachineDetectionError as error:
        return dataclasses.replace(step, status=f"machine detection failed: {error}")
    step = _select_inputs(step, source, gate_context)
    if step.status:
        return step
    pdir, environment, pver = step.artifact, step.environment, step.target_version
    try:
        dver = _deployed_version(step)
    except (OSError, ValueError) as error:
        return dataclasses.replace(step, status=f"source deployment receipt invalid: {error}")
    if name != "agent-bridge" and not force and pver and dver and reconcile._versions_equal(dver, pver):
        return dataclasses.replace(step, status="SKIPPED (current)")
    context = environment.get("COPILOT_EXTENSIONS_CONTEXT")
    if context is not None:
        if force:
            return dataclasses.replace(
                step, status="forced namespaced runtime reconciliation is unsupported",
            )
        try:
            built = reconcile.runtime_installer_argv(pdir, context=Path(context))
        except ValueError as error:
            return dataclasses.replace(step, status=str(error))
        if built is None:
            return dataclasses.replace(step, status="installer not found")
        _, argv = built
    else:
        prefix = _shell_prefix(pdir, platform_name, allow_init=True)
        if isinstance(prefix, str):
            return dataclasses.replace(step, status=prefix)
        shell_prefix, has_update = prefix
        if platform_name != "windows":
            shell_prefix[1] = Path(shell_prefix[1]).as_posix()
        argv = [*shell_prefix, *(["update"] if has_update else (
            ["-Force" if platform_name == "windows" else "--force"] if force else []
        ))]
    try:
        deadline = runtime._installer_timeout(name, environment, pdir)
    except (OSError, ValueError) as error:
        return dataclasses.replace(step, status=runtime.installer_deadline_status(error))
    return dataclasses.replace(step, argv=tuple(argv), deadline_s=deadline)


def _shell_prefix(
    pdir: Path, platform_name: str, *, allow_init: bool = False,
) -> tuple[list[str], bool] | str:
    windows = platform_name == "windows"
    extension = "ps1" if windows else "sh"
    installer = pdir / "scripts" / f"install.{extension}"
    has_update = installer.exists()
    if allow_init and not has_update:
        installer = pdir / "scripts" / f"init.{extension}"
    if not installer.exists():
        return "installer not found"
    if windows:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return "powershell not found"
        return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)], has_update
    return ["bash", str(installer)], has_update


def plan_module(
    name: str, pdir: Path, platform_name: str, *, force: bool,
    gate_context: Path | None = None,
    source: PreparedRuntimeSource | None = None,
) -> RuntimeUpdateStep:
    from . import update_runtime as runtime

    step = RuntimeUpdateStep(name, "module_update", pdir)
    if not pdir.is_dir():
        return dataclasses.replace(step, status="source dir not found")
    scope = reconcile.manifest_runtime_scope(pdir)
    if scope is None:
        return dataclasses.replace(step, status="invalid runtimeScope")
    if scope == "none":
        return dataclasses.replace(step, status="SKIPPED (payload-only)")
    step = dataclasses.replace(step, eligible=True)
    try:
        if runtime._machine_gate_blocks(name, scope, gate_context):
            return dataclasses.replace(step, status=runtime.MACHINE_GATED_SKIP, eligible=False)
    except runtime.MachineDetectionError as error:
        return dataclasses.replace(step, status=f"machine detection failed: {error}")
    step = _select_inputs(step, source, gate_context)
    if step.status:
        return step
    pdir, environment, pver = step.artifact, step.environment, step.target_version
    try:
        dver = _deployed_version(step)
    except (OSError, ValueError) as error:
        return dataclasses.replace(step, status=f"source deployment receipt invalid: {error}")
    if name != "agent-bridge" and not force and pver and dver and pver == dver:
        return dataclasses.replace(step, status="SKIPPED (current)")
    # Modules historically use install/update even in a selected cell. Never
    # replace that contract with the registered-runtime init/cell-provision path.
    prefix = _shell_prefix(pdir, platform_name)
    if isinstance(prefix, str):
        return dataclasses.replace(step, status=prefix)
    shell_prefix, _ = prefix
    update_args = ["update"]
    if reconcile._zero_downtime_update(pdir):
        if platform_name == "windows":
            update_args.append("-ZeroDowntime")
        elif runtime.posix_zero_downtime_flag_supported(pdir):
            update_args.append("--zero-downtime")
    try:
        deadline = runtime._installer_timeout(name, environment, pdir)
    except (OSError, ValueError) as error:
        return dataclasses.replace(step, status=runtime.installer_deadline_status(error))
    return dataclasses.replace(
        step, argv=tuple([*shell_prefix, *update_args]),
        fallback_argv=() if name == "agent-bridge" else tuple([*shell_prefix, "install"]),
        deadline_s=deadline,
    )


def plan_modules(
    plugin_dir: Path, platform_name: str, skip_modules: list[str] | None,
    *, force: bool = False, targets: Mapping[str, _RegisteredPluginTarget] | None = None,
    force_runtimes: Collection[str] = (),
    sources: Mapping[str, PreparedRuntimeSource] | None = None,
    selected_plugins: Collection[str] = (),
) -> tuple[RuntimeUpdateStep, ...]:
    manifest = plugin_dir / "modules.json"
    if not manifest.exists():
        return ()
    try:
        data = json.loads(manifest.read_text())
        modules = data.get("modules", [])
        if not isinstance(modules, list) or any(
            not isinstance(mod, dict) or not isinstance(mod.get("name"), str)
            or not isinstance(mod.get("source", mod["name"]), str)
            for mod in modules
        ):
            raise ValueError("modules must contain named source records")
    except (OSError, ValueError, AttributeError) as error:
        return (RuntimeUpdateStep(
            "modules.json", "module_update", plugin_dir, status=f"unreadable: {error}",
        ),)
    if skip_modules is not None and not skip_modules:
        return ()
    steps = []
    for mod in modules:
        name = mod["name"]
        if selected_plugins and name not in selected_plugins:
            continue
        pdir = plugin_dir.parent / mod.get("source", name)
        target = targets.get(name) if targets is not None else None
        if skip_modules and name in skip_modules:
            step = RuntimeUpdateStep(name, "module_update", pdir, status="SKIPPED")
        elif targets is not None and (target is None or not target.required):
            step = RuntimeUpdateStep(
                name, "module_update", pdir, status="SKIPPED (inactive inventory)", required=False,
            )
        else:
            step = plan_module(
                name, pdir, platform_name, force=force or name in force_runtimes,
                gate_context=target.context if target else None,
                **({"source": sources.get(name)} if sources is not None else {}),
            )
        steps.append(step)
    return tuple(steps)


def plan_portfolio(
    plugin_dir: Path, platform_name: str, skip_modules: list[str] | None, *,
    force: bool, targets: Mapping[str, _RegisteredPluginTarget],
    force_runtimes: Collection[str], sources: Mapping[str, PreparedRuntimeSource] | None = None,
    selected_plugins: Collection[str] = (),
    isolated_engine: bool = False,
) -> tuple[RuntimeUpdateStep | None, tuple[RuntimeUpdateStep, ...], tuple[RuntimeUpdateStep, ...]]:
    """Collect the engine, module and remaining active registered runtime owners."""
    engine = plan_engine(
        plugin_dir, platform_name, force=force or "agent-worktrees" in force_runtimes,
        **({"source": sources.get("agent-worktrees")} if sources is not None else {}),
    ) if not selected_plugins or "agent-worktrees" in selected_plugins else None
    if isolated_engine and engine is not None and not engine.blocked:
        from .installation_updates import isolated_engine_step

        try:
            engine = isolated_engine_step(engine)
        except (OSError, ValueError) as error:
            engine = dataclasses.replace(engine, status=f"isolated engine update blocked: {error}")
    modules = () if set(selected_plugins) == {"agent-worktrees"} else plan_modules(
        plugin_dir, platform_name, skip_modules, force=force,
        targets=targets, force_runtimes=force_runtimes,
        **({"sources": sources} if sources is not None else {}),
        **({"selected_plugins": selected_plugins} if selected_plugins else {}),
    )
    excluded = {step.name for step in modules} | {"agent-worktrees"} | set(skip_modules or ())
    registered = tuple(
        plan_registered(
            name, platform_name, force=force or name in force_runtimes,
            gate_context=target.context,
            **({"source": sources.get(name)} if sources is not None else {}),
        )
        for name, target in sorted(targets.items())
        if target.required and name not in excluded
        and not (skip_modules is not None and not skip_modules)
    )
    return engine, modules, registered


def plan_engine(
    plugin_dir: Path, platform_name: str, *, force: bool,
    source: PreparedRuntimeSource | None = None, project_root: Path | None = None,
) -> RuntimeUpdateStep:
    from . import update_cli

    step = RuntimeUpdateStep("agent-worktrees", "engine_runtime", plugin_dir, eligible=True)
    step = _select_inputs(step, source, project_root)
    if step.status:
        return step
    plugin_dir, root = step.artifact, step.runtime_root
    environment = dict(step.environment)
    environment["AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION"] = "1"
    step = dataclasses.replace(step, environment=environment)
    pver = step.target_version
    try:
        dver = _deployed_version(step)
    except (OSError, ValueError) as error:
        return dataclasses.replace(step, status=f"source deployment receipt invalid: {error}")
    if not force and pver and pver == dver:
        if "COPILOT_EXTENSIONS_CONTEXT" in environment or not reconcile.hook_shims_drifted(plugin_dir):
            return dataclasses.replace(step, status="SKIPPED (current)")
    prefix = _shell_prefix(plugin_dir, platform_name)
    if isinstance(prefix, str):
        return dataclasses.replace(step, status=prefix)
    shell_prefix, _ = prefix
    argv = [*shell_prefix, "update", "-InstallDir" if platform_name == "windows" else "--install-dir",
            str(root)]
    try:
        deadline = max(600, update_cli._installer_timeout("agent-worktrees", environment, plugin_dir))
    except (OSError, ValueError) as error:
        return dataclasses.replace(step, status=update_cli.installer_deadline_status(error))
    return dataclasses.replace(step, argv=tuple(argv), deadline_s=deadline)
