"""Explicit selected installation repair; discovery and observation never install."""

from __future__ import annotations

import os
import math
import hashlib
import platform as host_platform
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from installer_readiness import build_plan, parse_readiness
from installer_readiness.model import (
    ConfigurationEmpty, DiscoveryReport, Invocation, Module, Platform,
    ReadinessState, Requirement, Restart,
)

from .installation_repair_admission import (
    HostAdmission, OwnerBinding, RepairAdmissionError, admit_host, digest, ordinary,
)
from .installation_repair_process import ActionResult, cancel_on_termination, run_action
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
        return {"state": self.state, "reason": self.reason}


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
            interpreter = shutil.which("pwsh") or shutil.which("powershell")
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
        interpreter = shutil.which("bash")
        if not interpreter:
            raise RepairAdmissionError("repair requires an existing Bash interpreter")
        return [interpreter, str(invocation.target), *invocation.arguments]
    with invocation.target.open("rb") as stream:
        shebang = stream.readline(256).rstrip(b"\r\n")
    if shebang in (b"#!/usr/bin/env sh", b"#!/usr/bin/env bash"):
        interpreter = shutil.which(shebang.rsplit(b" ", 1)[1].decode("ascii"))
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
    if owner.payload_root_environment:
        result[owner.payload_root_environment] = str(owner.installation.payload_root)
    if readonly and owner.no_self_provision:
        result[owner.no_self_provision] = "1"
    elif owner.no_self_provision:
        result.pop(owner.no_self_provision, None)
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
        argv, executable, content_digest, target_digest = _admit_invocation(invocation, platform)
    except (RepairAdmissionError, OSError):
        return ActionResult("invocation-refused", None)
    purpose = "readiness" if readonly else "installer"
    if owner.execution_bindings.get(module.qualified_id + ":" + purpose) != digest({
        "argv": argv, "executable": str(executable), "sha256": content_digest,
        "targetSha256": target_digest,
    }):
        return ActionResult("invocation-refused", None)
    return run_action(
        argv, cwd=owner.installation.payload_root, environment=_environment(owner, readonly=readonly),
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
            try:
                argv, executable, content_digest, target_digest = _admit_invocation(
                    actions[platform], platform,
                )
                execution[module.qualified_id + ":" + purpose] = {
                    "argv": argv, "executable": str(executable), "sha256": content_digest,
                    "targetSha256": target_digest,
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
    return {
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
    }, owners


def _readmit_owner(
    project_root: Path, modules: Sequence[str], platform: Platform, plan_digest: str,
    owner_id: str, home: Path | None,
) -> OwnerBinding:
    fresh = admit_host(project_root, home=home)
    fresh_selected = _closure(fresh, modules, platform)
    fresh_inputs, owners = _binding_inputs(fresh, fresh_selected, platform)
    if digest(fresh_inputs) != plan_digest:
        raise RepairAdmissionError("repair owner binding changed")
    return owners[owner_id]


@cancel_on_termination()
def repair(
    project_root: Path, *, modules: Sequence[str] = (), apply: bool = False,
    expected_plan_digest: str | None = None, home: Path | None = None,
    readiness_timeout: float = 15, installer_timeout: float = 300,
    confirm_restart: str | None = None,
) -> dict[str, object]:
    """Serialize apply and explicit restart confirmation against durable obligations."""
    journal = RestartJournal(home if home is not None else Path.home())
    try:
        if confirm_restart is not None:
            if apply or modules or expected_plan_digest or not isinstance(confirm_restart, str):
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
                    expected_plan_digest=expected_plan_digest, home=home,
                    readiness_timeout=readiness_timeout, installer_timeout=installer_timeout,
                    journal=journal,
                )
        journal.load()
        return _repair(
            project_root, modules=modules, apply=apply,
            expected_plan_digest=expected_plan_digest, home=home,
            readiness_timeout=readiness_timeout, installer_timeout=installer_timeout,
            journal=journal,
        )
    except OSError as error:
        raise RepairAdmissionError("installation repair state cannot be accessed or persisted") from error


def _repair(
    project_root: Path, *, modules: Sequence[str], apply: bool,
    expected_plan_digest: str | None, home: Path | None,
    readiness_timeout: float, installer_timeout: float, journal: RestartJournal,
) -> dict[str, object]:
    """Reconstruct host admission and invoke only the explicit selected closure."""
    if (
        type(apply) is not bool or isinstance(modules, str) or len(modules) > 256
        or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in modules)
        or any(not math.isfinite(value) or not 0 < value <= 3600
               for value in (readiness_timeout, installer_timeout))
    ):
        raise RepairAdmissionError("repair selection or timeout is invalid")
    if apply and (not modules or not expected_plan_digest):
        raise RepairAdmissionError("repair apply requires qualified modules and the expected plan digest")
    platform = current_platform()
    host = admit_host(project_root, home=home)
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
    interrupted = False
    for module in selected:
        owner = owners.get(module.owner.owner_id)
        observation = (
            _observe(module, owner, platform, readiness_timeout)
            if owner else Observation("unknown", "owner-not-admitted")
        )
        outcomes[module.qualified_id].before = observation
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
            fresh_owner = _readmit_owner(
                project_root, modules, platform, plan_digest, module.owner.owner_id, home,
            )
            if not refresh_readiness and _satisfies(module, observation):
                continue
            if refresh_readiness:
                observation = _observe(module, fresh_owner, platform, readiness_timeout)
                outcome.after = observation
                if observation.reason == "readiness-interrupted":
                    interrupted = True
                    continue
                fresh_owner = _readmit_owner(
                    project_root, modules, platform, plan_digest, module.owner.owner_id, home,
                )
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
        if after.reason == "readiness-interrupted":
            interrupted = True
            continue
        try:
            _readmit_owner(
                project_root, modules, platform, plan_digest, module.owner.owner_id, home,
            )
        except KeyboardInterrupt:
            outcome.after = Observation("unknown", "owner-revalidation-interrupted")
            interrupted = True
        except RepairAdmissionError:
            outcome.after = Observation("unknown", "owner-binding-changed")
            execution_halted = True
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
        "platform": platform.value, "apply": apply, "planDigest": plan_digest,
        "interrupted": interrupted,
        "restarts": journal.evidence(),
        "status": ("discovered" if not selected else "observed") if not apply else (
            "ready" if satisfied else "incomplete" if not required_satisfied else "optional-incomplete"
        ),
        "requiredSatisfied": required_satisfied,
        "selected": [module.qualified_id for module in selected],
        "available": sorted(module.qualified_id for module in host.report.modules),
        "refusedOwners": dict(host.refused),
        "discoveryFindings": sorted({finding.code for finding in host.report.findings}),
        "outcomes": [outcome.evidence() for outcome in outcomes.values()],
    }
