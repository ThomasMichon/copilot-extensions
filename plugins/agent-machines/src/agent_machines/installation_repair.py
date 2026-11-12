"""Explicit selected installation repair; discovery and observation never install."""

from __future__ import annotations

import os
import math
import hashlib
import platform as host_platform
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from configuration_context.readiness import (
    PROBE_CONTRACT, PROBE_ENV, ProbeContractError, payload_contract, unqualified_output,
)
from installer_readiness import build_plan, parse_readiness
from installer_readiness.model import (
    ConfigurationEmpty, DiscoveryReport, Invocation, Module, Platform,
    ReadinessState, Requirement, Restart,
)

from .installation_repair_admission import (
    HostAdmission, OwnerBinding, RepairAdmissionError, admit_host, digest, ordinary,
)
from .installation_repair_process import (
    ActionResult, cancel_on_termination, path_interpreter, run_action,
)
from .installation_repair_restarts import RestartJournal


def current_platform() -> Platform:
    if sys.platform == "win32":
        return Platform.WINDOWS
    if sys.platform == "darwin":
        return Platform.MACOS
    if sys.platform.startswith("linux"):
        containerized = bool(os.environ.get("container")) or any(
            Path(marker).is_file()
            for marker in ("/.dockerenv", "/run/.containerenv")
        )
        return Platform.WSL if (
            not containerized and (
                os.environ.get("WSL_DISTRO_NAME")
                or "microsoft" in host_platform.release().lower()
            )
        ) else Platform.LINUX
    raise RepairAdmissionError("installation repair does not support this native platform")


@dataclass(frozen=True)
class Observation:
    state: str
    reason: str

    def evidence(self) -> dict[str, str]:
        result = {"state": self.state, "reason": self.reason}
        if self.reason == "readiness-isolation-unqualified":
            result["recovery"] = "update-owning-installation"
        return result


@dataclass
class Outcome:
    module: Module
    before: Observation
    attempted: bool = False
    installer: dict[str, str | int | None] | None = None
    after: Observation | None = None
    blocked_by: tuple[str, ...] = ()
    restart_required: bool = False

    @property
    def observed(self) -> Observation:
        return self.after or self.before

    @property
    def installer_succeeded(self) -> bool:
        return self.installer is None or (
            self.installer.get("status") == "exited" and self.installer.get("exitCode") == 0
        )

    def evidence(self) -> dict[str, object]:
        return {
            "module": self.module.qualified_id,
            "classification": self.module.classification.value,
            "observedBefore": self.before.evidence(), "selected": True,
            "attempted": self.attempted, "installer": self.installer,
            "observedAfter": self.after.evidence() if self.after else None,
            "blockedBy": list(self.blocked_by), "restart": self.module.restart.value,
            "restartRequired": self.restart_required,
        }


def _argv(invocation: Invocation, platform: Platform) -> list[str]:
    ordinary(invocation.target)
    suffix = invocation.target.suffix.lower()
    if platform is Platform.WINDOWS:
        if suffix == ".ps1":
            interpreter = path_interpreter("pwsh") or path_interpreter("powershell")
            if not interpreter:
                raise RepairAdmissionError("repair requires an existing PowerShell interpreter")
            return [interpreter, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                    str(invocation.target), *invocation.arguments]
        if suffix == ".cmd":
            shell = os.environ.get("ComSpec")
            if not shell or not Path(shell).is_absolute():
                raise RepairAdmissionError("repair requires the Windows command interpreter")
            if any(re.search(r'[%!\r\n"&|<>^]', argument) for argument in (
                str(invocation.target), *invocation.arguments,
            )):
                raise RepairAdmissionError("repair command arguments are not safe CMD literals")
            command = " ".join('"' + argument + '"' for argument in (
                str(invocation.target), *invocation.arguments,
            ))
            return [shell, "/d", "/s", "/c", '"' + command + '"']
        raise RepairAdmissionError("repair Windows action has an unsupported target")
    if invocation.kind == "payload-script":
        interpreter = path_interpreter("bash")
        if not interpreter:
            raise RepairAdmissionError("repair requires an existing Bash interpreter")
        return [interpreter, str(invocation.target), *invocation.arguments]
    with invocation.target.open("rb") as stream:
        shebang = stream.readline(256).rstrip(b"\r\n")
    if shebang in (b"#!/usr/bin/env sh", b"#!/usr/bin/env bash"):
        interpreter = path_interpreter(shebang.rsplit(b" ", 1)[1].decode("ascii"))
    elif shebang in (
        b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/sh", b"#!/usr/bin/bash",
    ):
        interpreter = os.fsdecode(shebang[2:])
    else:
        raise RepairAdmissionError("repair command has an unsupported interpreter declaration")
    if not interpreter:
        raise RepairAdmissionError("repair requires the declared command interpreter")
    return [interpreter, str(invocation.target), *invocation.arguments]


def _environment(owner: OwnerBinding, *, readonly: bool) -> dict[str, str]:
    result = dict(owner.environment)
    result.pop(PROBE_ENV, None)
    if owner.payload_root_environment:
        result[owner.payload_root_environment] = str(owner.installation.payload_root)
    if readonly and owner.no_self_provision:
        result[owner.no_self_provision] = "1"
    elif owner.no_self_provision:
        result.pop(owner.no_self_provision, None)
    if readonly:
        result[PROBE_ENV] = PROBE_CONTRACT
        paths = [entry for entry in os.get_exec_path(result) if Path(entry).is_absolute()]
        for key in list(result):
            if key.upper() in {"PATH", "BASH_ENV"}:
                result.pop(key)
        # An empty PATH would re-enable current-directory lookup on POSIX.
        result["PATH"] = os.pathsep.join(paths) or str(Path(os.devnull).absolute())
        result["NoDefaultCurrentDirectoryInExePath"] = "1"
    return result


def _admit_invocation(
    invocation: Invocation, platform: Platform,
) -> tuple[list[str], Path, str, str]:
    argv = _argv(invocation, platform)
    declared_executable = Path(argv[0]).absolute()
    executable = declared_executable.resolve(strict=True)
    ordinary(executable)
    if executable.stat().st_size > 64 * 1024 * 1024:
        raise RepairAdmissionError("repair interpreter exceeds its content bound")
    content = hashlib.sha256()
    with executable.open("rb") as stream:
        remaining = 64 * 1024 * 1024 + 1
        while block := stream.read(min(1024 * 1024, remaining)):
            remaining -= len(block)
            if remaining <= 0:
                raise RepairAdmissionError("repair interpreter exceeds its content bound")
            content.update(block)
    with invocation.target.open("rb") as stream:
        target_content = stream.read(8 * 1024 * 1024 + 1)
    if len(target_content) > 8 * 1024 * 1024:
        raise RepairAdmissionError("repair action exceeds its content bound")
    argv[0] = str(declared_executable)
    return argv, executable, content.hexdigest(), hashlib.sha256(target_content).hexdigest()


def _invoke(
    module: Module, owner: OwnerBinding, platform: Platform, *, readonly: bool,
    timeout: float,
) -> ActionResult:
    invocation = (module.readiness if readonly else module.installer)[platform]
    try:
        if readonly:
            ordinary(owner.project_root, directory=True)
        isolation = payload_contract(
            owner.installation.payload_root, owner.installation.plugin_id,
        ) if readonly else None
        argv, executable, content_digest, target_digest = _admit_invocation(invocation, platform)
    except ProbeContractError:
        return ActionResult("isolation-unqualified", None)
    except (RepairAdmissionError, OSError):
        return ActionResult("invocation-refused", None)
    purpose = "readiness" if readonly else "installer"
    execution = {
        "argv": argv, "executable": str(executable), "sha256": content_digest,
        "targetSha256": target_digest,
    }
    if isolation is not None:
        execution["readinessIsolationSha256"] = isolation.sha256
    if owner.execution_bindings.get(module.qualified_id + ":" + purpose) != digest(execution):
        return ActionResult("invocation-refused", None)
    return run_action(
        argv, cwd=owner.project_root if readonly else owner.installation.payload_root,
        environment=_environment(owner, readonly=readonly),
        timeout=timeout, readonly=readonly,
        cmd=platform is Platform.WINDOWS and invocation.target.suffix.lower() == ".cmd",
    )


def _observe(module: Module, owner: OwnerBinding, platform: Platform, timeout: float) -> Observation:
    if owner.refusal:
        return Observation("unknown", owner.refusal)
    if platform not in module.platforms:
        return Observation("unsupported", "platform-not-declared")
    try:
        result = _invoke(module, owner, platform, readonly=True, timeout=timeout)
    except KeyboardInterrupt:
        return Observation("unknown", "readiness-interrupted")
    if result.status != "exited":
        return Observation("unknown", "readiness-" + result.status)
    if result.exit_code == 126 and unqualified_output(result.stdout):
        return Observation("unknown", "readiness-isolation-unqualified")
    try:
        parsed = parse_readiness(result.stdout)
    except ValueError:
        return Observation("unknown", "readiness-invalid-output")
    if parsed.module_id != module.module_id:
        return Observation("unknown", "readiness-module-mismatch")
    if result.exit_code != 0 and parsed.state is not ReadinessState.FAILED:
        return Observation("unknown", "readiness-exit-state-conflict")
    return Observation(parsed.state.value, "validated-owner-readiness")


def _satisfies(module: Module, observation: Observation) -> bool:
    return observation.state == "ready" or (
        observation.state == "configuration-empty"
        and module.configuration_empty is ConfigurationEmpty.SATISFIED
    )


def _closure(host: HostAdmission, requested: Sequence[str], platform: Platform) -> tuple[Module, ...]:
    by_id: dict[str, Module] = {}
    duplicates: set[str] = set()
    for module in host.report.modules:
        if module.qualified_id in by_id:
            duplicates.add(module.qualified_id)
        by_id[module.qualified_id] = module
    selected: dict[str, Module] = {}
    visiting: set[str] = set()

    def include(qualified: str):
        if not 1 <= len(qualified) <= 256:
            raise RepairAdmissionError("selected module identity exceeds its bound")
        if qualified in duplicates or qualified not in by_id:
            raise RepairAdmissionError("selected module identity is missing or ambiguous")
        if qualified in visiting:
            raise RepairAdmissionError("selected module dependencies contain a cycle")
        if qualified in selected:
            return
        if len(selected) + len(visiting) >= 256:
            raise RepairAdmissionError("selected module closure exceeds its bound")
        module = by_id[qualified]
        visiting.add(qualified)
        for dependency in module.dependencies:
            include(module.owner.provenance.marketplace_id + "::" + dependency)
        visiting.remove(qualified)
        selected[qualified] = module

    for qualified in sorted(set(requested)):
        include(qualified)
    failures = [
        finding for finding in host.report.findings
        if finding.module_id in selected
        or finding.owner in {module.owner.owner_id for module in selected.values()}
    ]
    if failures:
        raise RepairAdmissionError("selected owner declarations did not pass discovery")
    report = DiscoveryReport(modules=tuple(selected.values()))
    # Reuse the library's lexical topological order, never its PLANNED-as-executed inference.
    return tuple(step.module for step in build_plan(report, platform).steps)


def _binding_inputs(
    host: HostAdmission, modules: Sequence[Module], platform: Platform,
) -> tuple[dict[str, object], dict[str, OwnerBinding]]:
    execution: dict[str, dict[str, object]] = {}
    for module in modules:
        if platform not in module.platforms:
            continue
        for purpose, actions in (("readiness", module.readiness), ("installer", module.installer)):
            if host.readiness_only and purpose != "readiness":
                continue
            try:
                isolation = payload_contract(
                    module.owner.payload_root, module.owner.plugin_id,
                ) if purpose == "readiness" else None
                argv, executable, content_digest, target_digest = _admit_invocation(
                    actions[platform], platform,
                )
                execution[module.qualified_id + ":" + purpose] = {
                    "argv": argv, "executable": str(executable), "sha256": content_digest,
                    "targetSha256": target_digest,
                }
                if isolation is not None:
                    execution[module.qualified_id + ":" + purpose][
                        "readinessIsolationSha256"
                    ] = isolation.sha256
            except ProbeContractError:
                execution[module.qualified_id + ":" + purpose] = {
                    "status": "isolation-unqualified",
                }
            except (RepairAdmissionError, OSError):
                execution[module.qualified_id + ":" + purpose] = {"status": "invocation-unavailable"}
    owners = {
        owner_id: replace(owner, execution_bindings={
            key: digest(value) for key, value in execution.items()
            if key.startswith(owner_id + "/")
        })
        for owner_id, owner in host.bindings.items()
    }
    bound: dict[str, object] = {
        "platform": platform.value,
        "execution": execution,
        "activationDigest": host.activation_digest,
        "owners": [
            host.bindings[owner].evidence()
            for owner in sorted({module.owner.owner_id for module in modules})
            if owner in host.bindings
        ],
        "modules": [{
            "id": module.qualified_id,
            "dependencies": list(module.dependencies),
            "classification": module.classification.value,
            "configurationEmpty": module.configuration_empty.value,
            "restart": module.restart.value,
            "platforms": [item.value for item in module.platforms],
            "installer": {
                item.value: {"kind": action.kind, "target": str(action.target),
                             "arguments": list(action.arguments)}
                for item, action in module.installer.items()
            },
            "readiness": {
                item.value: {"kind": action.kind, "target": str(action.target),
                             "arguments": list(action.arguments)}
                for item, action in module.readiness.items()
            },
        } for module in modules],
    }
    if host.readiness_only:
        bound["purpose"] = "required-readiness-only"
    return bound, owners


def _required_modules(host: HostAdmission) -> list[str]:
    if (
        not host.activation_complete or host.refused or host.report.findings
        or (
            not host.readiness_only and any(owner.refusal for owner in host.bindings.values())
        )
    ):
        raise RepairAdmissionError("required selection needs a complete enabled-owner inventory")
    return [
        module.qualified_id for module in host.report.modules
        if module.classification is Requirement.REQUIRED
    ]


def _readmit(
    project_root: Path, modules: Sequence[str], platform: Platform, plan_digest: str,
    home: Path | None, required: bool, *, readiness_only: bool = False,
) -> dict[str, OwnerBinding]:
    fresh = admit_host(project_root, home=home, readiness_only=readiness_only)
    fresh_selected = _closure(fresh, _required_modules(fresh) if required else modules, platform)
    fresh_inputs, owners = _binding_inputs(fresh, fresh_selected, platform)
    if digest(fresh_inputs) != plan_digest:
        raise RepairAdmissionError("repair owner binding changed")
    return owners


@cancel_on_termination()
def repair(
    project_root: Path, *, modules: Sequence[str] = (), apply: bool = False,
    required: bool = False,
    expected_plan_digest: str | None = None, home: Path | None = None,
    readiness_timeout: float = 15, installer_timeout: float = 300,
    confirm_restart: str | None = None,
    on_observation: Callable[[list[dict[str, object]]], None] | None = None,
    readiness_only: bool = False,
) -> dict[str, object]:
    """Serialize apply and explicit restart confirmation against durable obligations."""
    if type(readiness_only) is not bool or (
        readiness_only and (apply or not required or modules or expected_plan_digest or confirm_restart)
    ):
        raise RepairAdmissionError("readiness-only observation requires read-only required selection")
    journal = RestartJournal(home if home is not None else Path.home())
    try:
        if confirm_restart is not None:
            if apply or modules or required or expected_plan_digest or not isinstance(confirm_restart, str):
                raise RepairAdmissionError("restart confirmation cannot also plan or apply repair")
            with journal.locked():
                confirmed = journal.evidence()
                journal.confirm(confirm_restart)
            return {
                "schema": "copilot-extensions.installation-repair", "version": 1,
                "status": "restart-confirmed", "interrupted": False,
                "confirmation": "operator-attestation", "confirmedRestarts": confirmed,
                "restarts": journal.evidence(),
            }
        if apply:
            with journal.locked():
                return _repair(
                    project_root, modules=modules, apply=apply,
                    required=required,
                    expected_plan_digest=expected_plan_digest, home=home,
                    readiness_timeout=readiness_timeout, installer_timeout=installer_timeout,
                    journal=journal,
                    on_observation=on_observation,
                    readiness_only=readiness_only,
                )
        journal.load()
        return _repair(
            project_root, modules=modules, apply=apply,
            required=required,
            expected_plan_digest=expected_plan_digest, home=home,
            readiness_timeout=readiness_timeout, installer_timeout=installer_timeout,
            journal=journal,
            on_observation=on_observation,
            readiness_only=readiness_only,
        )
    except OSError as error:
        raise RepairAdmissionError("installation repair state cannot be accessed or persisted") from error


def _repair(
    project_root: Path, *, modules: Sequence[str], apply: bool,
    required: bool,
    expected_plan_digest: str | None, home: Path | None,
    readiness_timeout: float, installer_timeout: float, journal: RestartJournal,
    on_observation: Callable[[list[dict[str, object]]], None] | None = None,
    readiness_only: bool = False,
) -> dict[str, object]:
    """Reconstruct host admission and invoke only the explicit selected closure."""
    if (
        type(apply) is not bool or type(required) is not bool
        or isinstance(modules, str) or len(modules) > 256
        or required and modules
        or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in modules)
        or any(not math.isfinite(value) or not 0 < value <= 3600
               for value in (readiness_timeout, installer_timeout))
    ):
        raise RepairAdmissionError("repair selection or timeout is invalid")
    if apply and (not (modules or required) or not expected_plan_digest):
        raise RepairAdmissionError("repair apply requires qualified modules and the expected plan digest")
    platform = current_platform()
    host = admit_host(project_root, home=home, readiness_only=readiness_only)
    if required:
        modules = _required_modules(host)
    selected = _closure(host, modules, platform)
    bound, owners = _binding_inputs(host, selected, platform)
    plan_digest = digest(bound)
    if apply and expected_plan_digest != plan_digest:
        raise RepairAdmissionError("repair plan inputs changed; no installer was invoked")
    outcomes = {
        module.qualified_id: Outcome(
            module=module, before=Observation("unknown", "readiness-not-observed"),
            restart_required=module.qualified_id in journal.pending,
        )
        for module in selected
    }
    if on_observation is not None:
        on_observation([outcome.evidence() for outcome in outcomes.values()])
    interrupted = False
    for module in selected:
        owner = owners.get(module.owner.owner_id)
        observation = (
            _observe(module, owner, platform, readiness_timeout)
            if owner else Observation("unknown", "owner-not-admitted")
        )
        outcomes[module.qualified_id].before = observation
        if on_observation is not None:
            on_observation([outcomes[module.qualified_id].evidence()])
        if observation.reason == "readiness-interrupted":
            interrupted = True
            break
    execution_halted = False
    for module in selected:
        outcome = outcomes[module.qualified_id]
        observation = outcome.before
        dependencies = [
            module.owner.provenance.marketplace_id + "::" + item for item in module.dependencies
        ]
        blockers = []
        for dependency in dependencies:
            dependency_outcome = outcomes[dependency]
            if (
                not _satisfies(dependency_outcome.module, dependency_outcome.observed)
                or dependency_outcome.restart_required
                or not dependency_outcome.installer_succeeded
                or dependency_outcome.blocked_by
            ):
                blockers.append(dependency)
        outcome.blocked_by = tuple(blockers)
        if not apply or interrupted or execution_halted or blockers or outcome.restart_required:
            continue
        owner = owners.get(module.owner.owner_id)
        if not owner or owner.refusal or platform not in module.platforms:
            continue
        refresh_readiness = any(item.attempted for item in outcomes.values())
        try:
            fresh_owner = _readmit(
                project_root, modules, platform, plan_digest, home, required,
                readiness_only=readiness_only,
            )[module.owner.owner_id]
            if not refresh_readiness and _satisfies(module, observation):
                continue
            if refresh_readiness:
                observation = _observe(module, fresh_owner, platform, readiness_timeout)
                outcome.after = observation
                if observation.reason == "readiness-interrupted":
                    interrupted = True
                    continue
                fresh_owner = _readmit(
                    project_root, modules, platform, plan_digest, home, required,
                    readiness_only=readiness_only,
                )[module.owner.owner_id]
                if _satisfies(module, observation):
                    continue
        except KeyboardInterrupt:
            outcome.after = Observation("unknown", "owner-revalidation-interrupted")
            interrupted = True
            continue
        except RepairAdmissionError:
            outcome.installer = {"status": "refused", "reason": "owner-binding-changed"}
            execution_halted = True
            continue
        if fresh_owner.mode == "core-active":
            # An ordinary init action is not permission to synthesize a cell lifecycle operation.
            outcome.installer = {"status": "refused", "reason": "active-core-action-not-declared"}
            continue
        try:
            if module.restart is not Restart.NONE:
                journal.require(module.qualified_id, module.restart.value, plan_digest)
                outcome.restart_required = True
            outcome.attempted = True
            if on_observation is not None:
                on_observation([outcome.evidence()])
            result = _invoke(
                module, fresh_owner, platform, readonly=False, timeout=installer_timeout,
            )
        except KeyboardInterrupt:
            outcome.installer = {"status": "interrupted", "exitCode": None}
            interrupted = True
            continue
        outcome.installer = {"status": result.status, "exitCode": result.exit_code}
        after = _observe(module, fresh_owner, platform, readiness_timeout)
        outcome.after = after
        if on_observation is not None:
            on_observation([outcome.evidence()])
        if after.reason == "readiness-interrupted":
            interrupted = True
            continue
        try:
            _readmit(
                project_root, modules, platform, plan_digest, home, required,
                readiness_only=readiness_only,
            )
        except KeyboardInterrupt:
            outcome.after = Observation("unknown", "owner-revalidation-interrupted")
            interrupted = True
        except RepairAdmissionError:
            outcome.after = Observation("unknown", "owner-binding-changed")
            execution_halted = True
    selection_refusal = None
    if required and not interrupted and not execution_halted:
        try:
            _readmit(
                project_root, modules, platform, plan_digest, home, required,
                readiness_only=readiness_only,
            )
        except KeyboardInterrupt:
            interrupted = True
        except RepairAdmissionError:
            selection_refusal = "required-inventory-changed"
            execution_halted = True
            for outcome in outcomes.values():
                outcome.after = Observation("unknown", selection_refusal)
    required_satisfied = not interrupted and not execution_halted and all(
        _satisfies(outcome.module, outcome.observed) and not outcome.restart_required
        and not outcome.blocked_by
        and outcome.installer_succeeded
        for outcome in outcomes.values() if outcome.module.classification is Requirement.REQUIRED
    )
    satisfied = not interrupted and not execution_halted and all(
        _satisfies(outcome.module, outcome.observed) and not outcome.restart_required
        and not outcome.blocked_by
        and outcome.installer_succeeded
        for outcome in outcomes.values()
    )
    return {
        "schema": "copilot-extensions.installation-repair", "version": 1,
        "projectRoot": str(project_root.resolve()), "requiredSelection": required,
        "platform": platform.value, "apply": apply, "planDigest": plan_digest,
        "interrupted": interrupted,
        "restarts": journal.evidence(),
        "status": ("observed" if selected or required else "discovered") if not apply else (
            "ready" if satisfied else "incomplete" if not required_satisfied else "optional-incomplete"
        ),
        "requiredSatisfied": required_satisfied,
        "selectionRefusal": selection_refusal,
        "selected": [module.qualified_id for module in selected],
        "available": sorted(module.qualified_id for module in host.report.modules),
        "refusedOwners": dict(host.refused),
        "discoveryFindings": sorted({finding.code for finding in host.report.findings}),
        "outcomes": [outcome.evidence() for outcome in outcomes.values()],
    }
