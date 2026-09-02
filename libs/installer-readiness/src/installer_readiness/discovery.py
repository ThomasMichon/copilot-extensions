"""Strict discovery for plugin-owned installer/readiness modules."""

from __future__ import annotations

import json
import os
import re
import stat
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from installation_context import (
    InstallationContextError,
    normalize_source,
    source_identity,
    validate_context_receipt,
    validate_namespace_receipt,
)

from .model import (
    ConfigurationEmpty,
    Decline,
    DiscoveryReport,
    Finding,
    Invocation,
    MarketplaceProvenance,
    Module,
    Platform,
    PluginInstallation,
    Requirement,
    Restart,
)

CONTRACT_SCHEMA = "copilot-extensions.installer-readiness"
CONTRACT_VERSION = 1
READINESS_SCHEMA = "copilot-extensions.module-readiness"
READINESS_VERSION = 1
PLUGIN_MANIFEST_PATHS = (("plugin.json",), (".claude-plugin", "plugin.json"))
PROJECT_SETTINGS_PATHS = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)
USER_SETTINGS_PATHS = (("settings.json",), ("settings.local.json",))
_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_MODULE_ID = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?/"
    r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$"
)
_COMMAND_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_PLUGIN_ID = re.compile(r"^agent-[a-z0-9-]+$")
_PYTHON_MODULE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_RUNTIME_ROOT = re.compile(r"^\.[a-z0-9-]+$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")
_PURPOSE = re.compile(r"^[A-Za-z0-9 ._/-]+$")
_OUTPUT_DIR = re.compile(r"^[a-z0-9][a-z0-9_./-]*$")
_MARKETPLACE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class SettingsLayer(str, Enum):
    """Settings path family and merge precedence."""

    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True)
class SettingsGroup:
    """One explicitly typed settings scope."""

    root: Path
    scope: str
    layer: SettingsLayer = SettingsLayer.PROJECT


@dataclass(frozen=True)
class _Enabled:
    plugin_id: str
    marketplace_key: str
    fingerprint: str
    scope: str
    source: str


@dataclass
class _SettingsValues:
    enabled: dict[str, tuple[bool, str]]
    marketplaces: dict[str, tuple[dict[str, Any], Path, Path]]
    findings: list[Finding]


class _ContractProblem(ValueError):
    pass


def _strict_json(path: Path) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        folded: dict[str, str] = {}
        for key, value in pairs:
            if key in result:
                raise _ContractProblem(f"duplicate property '{key}'")
            casefolded = key.casefold()
            if casefolded in folded:
                raise _ContractProblem(
                    f"properties '{folded[casefolded]}' and '{key}' differ only by case"
                )
            result[key] = value
            folded[casefolded] = key
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _ContractProblem(str(error)) from error


def _finding(
    code: str,
    message: str,
    source: Path | str,
    *,
    owner: str | None = None,
    module_id: str | None = None,
    remedy: str | None = None,
) -> Finding:
    return Finding(
        code=code,
        message=message,
        source=str(source),
        owner=owner,
        module_id=module_id,
        remedy=remedy,
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _ContractProblem(f"{label} must be an object")
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _ContractProblem(f"{label} has unknown fields: {', '.join(unknown)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise _ContractProblem(f"{label} must be a non-empty string without NUL")
    return value.strip()


def _version(value: Any, expected: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise _ContractProblem(f"{label} must be integer {expected}")


def _regular_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as error:
        raise _ContractProblem(f"{label} is unavailable: {error}") from error
    is_reparse = bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse:
        raise _ContractProblem(f"{label} must be a regular non-link file")
    return path


def _payload_path(root: Path, raw: Any, label: str) -> Path:
    text = _string(raw, label)
    relative = Path(text)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise _ContractProblem(f"{label} must be a contained relative payload path")
    try:
        payload = root.resolve(strict=True)
        target = (payload / relative).resolve(strict=True)
        target.relative_to(payload)
    except (OSError, ValueError) as error:
        raise _ContractProblem(f"{label} is not contained in the payload: {error}") from error
    return _regular_file(target, label)


def _plugin_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    for parts in PLUGIN_MANIFEST_PATHS:
        path = root.joinpath(*parts)
        if path.is_file():
            return (
                _regular_file(path, "plugin manifest"),
                _object(_strict_json(path), "plugin manifest"),
            )
    raise _ContractProblem("plugin manifest is missing")


def _payload_command(
    value: Any,
    *,
    label: str,
) -> tuple[str, str, str]:
    data = _object(value, label)
    command = _string(data.get("command"), f"{label}.command")
    module = _string(data.get("module"), f"{label}.module")
    purpose = _string(data.get("purpose"), f"{label}.purpose")
    if not _COMMAND_ID.fullmatch(command):
        raise _ContractProblem(f"{label}.command is invalid")
    if not _PYTHON_MODULE.fullmatch(module):
        raise _ContractProblem(f"{label}.module is invalid")
    if not _PURPOSE.fullmatch(purpose):
        raise _ContractProblem(f"{label}.purpose is invalid")
    return command, module, purpose


def _payload_commands(root: Path) -> tuple[dict[str, Path], str]:
    if not (root / "payload-invocation.json").exists():
        raise _ContractProblem(
            "payload-command requires payload-invocation.json in the owning payload"
        )
    path = _payload_path(root, "payload-invocation.json", "payload invocation manifest")
    data = _object(_strict_json(path), "payload invocation manifest")
    if data.get("schema") != "copilot-extensions.payload-invocation":
        raise _ContractProblem("payload invocation manifest has unsupported schema/version")
    manifest_version = data.get("version")
    if (
        not isinstance(manifest_version, int)
        or isinstance(manifest_version, bool)
        or manifest_version not in {1, 2}
    ):
        raise _ContractProblem("payload invocation version must be integer 1 or 2")
    runtime_root_field = (
        "runtimeRoot" if manifest_version == 1 else "legacyRuntimeRoot"
    )
    runtime_root = data.get(runtime_root_field)
    if not isinstance(runtime_root, str) or not _RUNTIME_ROOT.fullmatch(runtime_root):
        raise _ContractProblem(
            f"payload invocation {runtime_root_field} is invalid"
        )
    if manifest_version == 1:
        if "legacyRuntimeRoot" in data or "installationContext" in data:
            raise _ContractProblem(
                "payload invocation version 1 cannot declare installation context"
            )
    elif (
        "runtimeRoot" in data
        or data.get("installationContext") not in {"legacy", "required"}
    ):
        raise _ContractProblem(
            "payload invocation version 2 installation context is invalid"
        )
    no_self_provision = data.get("noSelfProvisionEnv")
    if (
        not isinstance(no_self_provision, str)
        or not _ENVIRONMENT_NAME.fullmatch(no_self_provision)
    ):
        raise _ContractProblem("payload invocation noSelfProvisionEnv is invalid")
    output_dir = data.get("outputDir", "bin")
    if (
        not isinstance(output_dir, str)
        or not output_dir
        or not _OUTPUT_DIR.fullmatch(output_dir)
        or ".." in Path(output_dir).parts
    ):
        raise _ContractProblem("payload invocation outputDir is invalid")
    raw_commands = data.get("commands")
    if raw_commands is None:
        command = _payload_command(data, label="payload command")
        raw_plugin = data.get("plugin", command[0])
        if raw_plugin != command[0]:
            raise _ContractProblem(
                "legacy payload plugin must equal command; use commands for "
                "distinct plugin identity"
            )
        parsed_commands = [command]
    else:
        if any(field in data for field in ("command", "module", "purpose")):
            raise _ContractProblem(
                "payload invocation commands cannot be combined with "
                "top-level command/module/purpose"
            )
        if not isinstance(raw_commands, list) or not raw_commands:
            raise _ContractProblem(
                "payload invocation commands must be a non-empty array"
            )
        parsed_commands = [
            _payload_command(item, label=f"payload commands[{index}]")
            for index, item in enumerate(raw_commands)
        ]
        raw_plugin = data.get("plugin")
    if not isinstance(raw_plugin, str) or not _PLUGIN_ID.fullmatch(raw_plugin):
        raise _ContractProblem("payload invocation plugin is invalid")
    commands: dict[str, Path] = {}
    for command, _module_name, _purpose in parsed_commands:
        if command in commands:
            raise _ContractProblem(f"payload command id is invalid or duplicate: {command}")
        commands[command] = Path(output_dir) / command
    windows_shim = data.get("windowsCatalogShim", "powershell")
    if windows_shim not in {"powershell", "cmd"}:
        raise _ContractProblem("payload invocation windowsCatalogShim is invalid")
    return commands, windows_shim


def _invocation(
    value: Any,
    *,
    root: Path,
    platform: Platform,
    command_loader: Callable[[], tuple[Mapping[str, Path], str]],
    label: str,
) -> Invocation:
    data = _object(value, label)
    _keys(data, {"kind", "path", "command", "arguments"}, label)
    kind = data.get("kind")
    if kind not in {"payload-script", "payload-command"}:
        raise _ContractProblem(
            f"{label}.kind must be payload-script or payload-command"
        )
    raw_arguments = data.get("arguments", [])
    if not isinstance(raw_arguments, list) or any(
        not isinstance(argument, str) or "\0" in argument for argument in raw_arguments
    ):
        raise _ContractProblem(f"{label}.arguments must be an array of strings")
    arguments = tuple(raw_arguments)
    if kind == "payload-script":
        if "command" in data:
            raise _ContractProblem(f"{label} cannot combine path and command")
        target = _payload_path(root, data.get("path"), f"{label}.path")
        expected_suffix = ".ps1" if platform is Platform.WINDOWS else ".sh"
        if target.suffix.casefold() != expected_suffix:
            raise _ContractProblem(
                f"{label}.path must use {expected_suffix} on {platform.value}"
            )
        return Invocation(kind=kind, target=target, arguments=arguments)
    if "path" in data:
        raise _ContractProblem(f"{label} cannot combine command and path")
    commands, windows_shim = command_loader()
    command = _string(data.get("command"), f"{label}.command")
    if not _COMMAND_ID.fullmatch(command) or command not in commands:
        raise _ContractProblem(
            f"{label}.command '{command}' is not declared by payload-invocation.json"
        )
    suffix = (
        ".cmd"
        if platform is Platform.WINDOWS and windows_shim == "cmd"
        else ".ps1"
        if platform is Platform.WINDOWS
        else ""
    )
    target = _payload_path(
        root,
        f"{commands[command]}{suffix}",
        f"{label}.command target",
    )
    if platform is not Platform.WINDOWS and not os.access(target, os.X_OK):
        raise _ContractProblem(f"{label}.command target is not executable")
    return Invocation(
        kind=kind,
        target=target,
        arguments=arguments,
        command_id=command,
    )


def _platform_invocations(
    value: Any,
    *,
    root: Path,
    platforms: tuple[Platform, ...],
    command_loader: Callable[[], tuple[Mapping[str, Path], str]],
    label: str,
) -> dict[Platform, Invocation]:
    data = _object(value, label)
    expected = {platform.value for platform in platforms}
    actual = set(data)
    unknown = sorted(actual - {platform.value for platform in Platform})
    if unknown:
        raise _ContractProblem(f"{label} has invalid platforms: {', '.join(unknown)}")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"undeclared {', '.join(extra)}")
        raise _ContractProblem(f"{label} platform mismatch: {'; '.join(details)}")
    return {
        platform: _invocation(
            data[platform.value],
            root=root,
            platform=platform,
            command_loader=command_loader,
            label=f"{label}.{platform.value}",
        )
        for platform in platforms
    }


def _module(
    value: Any,
    *,
    owner: PluginInstallation,
    source: Path,
    command_loader: Callable[[], tuple[Mapping[str, Path], str]],
) -> Module:
    data = _object(value, "module")
    _keys(
        data,
        {
            "id",
            "platforms",
            "classification",
            "installer",
            "readiness",
            "dependsOn",
            "restart",
        },
        "module",
    )
    module_id = _string(data.get("id"), "module.id")
    if not _MODULE_ID.fullmatch(module_id) or module_id.split("/", 1)[0] != owner.plugin_id:
        raise _ContractProblem(
            "module.id must be '<owner-plugin>/<local-id>' and match the manifest owner"
        )
    raw_platforms = data.get("platforms")
    if not isinstance(raw_platforms, list) or not raw_platforms:
        raise _ContractProblem("module.platforms must be a non-empty array")
    try:
        platforms = tuple(Platform(value) for value in raw_platforms)
    except (TypeError, ValueError) as error:
        raise _ContractProblem("module.platforms contains an invalid platform") from error
    if len(platforms) != len(set(platforms)):
        raise _ContractProblem("module.platforms contains duplicates")
    try:
        classification = Requirement(data.get("classification"))
    except ValueError as error:
        raise _ContractProblem(
            "module.classification must be required or optional"
        ) from error
    try:
        restart = Restart(data.get("restart"))
    except ValueError as error:
        raise _ContractProblem(
            "module.restart must be none, shell, session, or machine"
        ) from error
    readiness_data = _object(data.get("readiness"), "module.readiness")
    _keys(
        readiness_data,
        {"schema", "version", "configurationEmpty", "invocations"},
        "module.readiness",
    )
    if readiness_data.get("schema") != READINESS_SCHEMA:
        raise _ContractProblem("module.readiness has unsupported schema/version")
    _version(
        readiness_data.get("version"),
        READINESS_VERSION,
        "module.readiness.version",
    )
    try:
        configuration_empty = ConfigurationEmpty(
            readiness_data.get("configurationEmpty")
        )
    except ValueError as error:
        raise _ContractProblem(
            "module.readiness.configurationEmpty must be satisfied or unsatisfied"
        ) from error
    if "dependsOn" not in data:
        raise _ContractProblem("module.dependsOn is required")
    raw_dependencies = data["dependsOn"]
    if not isinstance(raw_dependencies, list):
        raise _ContractProblem("module.dependsOn must be an array")
    dependencies: list[str] = []
    for dependency in raw_dependencies:
        dependency_id = _string(dependency, "module.dependsOn entry")
        if not _MODULE_ID.fullmatch(dependency_id):
            raise _ContractProblem(
                "module.dependsOn entries must be '<plugin>/<module>' ids"
            )
        dependencies.append(dependency_id)
    if len(dependencies) != len(set(dependencies)):
        raise _ContractProblem("module.dependsOn contains duplicates")
    return Module(
        module_id=module_id,
        owner=owner,
        platforms=platforms,
        classification=classification,
        installer=_platform_invocations(
            data.get("installer"),
            root=owner.payload_root,
            platforms=platforms,
            command_loader=command_loader,
            label="module.installer",
        ),
        readiness=_platform_invocations(
            readiness_data.get("invocations"),
            root=owner.payload_root,
            platforms=platforms,
            command_loader=command_loader,
            label="module.readiness.invocations",
        ),
        configuration_empty=configuration_empty,
        dependencies=tuple(dependencies),
        restart=restart,
        source=source,
    )


def _discover_installation(
    installation: PluginInstallation,
) -> tuple[list[Module], Decline | None, list[Finding], bool]:
    findings: list[Finding] = []
    is_machine_gated = False
    try:
        manifest_path, plugin = _plugin_manifest(installation.payload_root)
        runtime_scope = plugin.get("runtimeScope")
        if runtime_scope is not None:
            runtime_scope = _string(runtime_scope, "plugin manifest runtimeScope")
            if runtime_scope not in {"machine-gated", "universal", "none"}:
                raise _ContractProblem(
                    "plugin manifest runtimeScope must be machine-gated, "
                    "universal, or none"
                )
        is_machine_gated = runtime_scope == "machine-gated"
        plugin_name = _string(plugin.get("name"), "plugin manifest name")
        if plugin_name != installation.plugin_id:
            raise _ContractProblem(
                f"plugin manifest names '{plugin_name}', expected '{installation.plugin_id}'"
            )
        reference = plugin.get("installerReadiness")
        if reference is None:
            if not is_machine_gated:
                return [], None, findings, False
            findings.append(
                _finding(
                    "missing-module-metadata",
                    "enabled machine-gated plugin has no installerReadiness manifest",
                    manifest_path,
                    owner=installation.owner_id,
                    remedy=(
                        "Add a supported or intentionally declined "
                        "installer-readiness manifest reference."
                    ),
                )
            )
            return [], None, findings, is_machine_gated
        contract_path = _payload_path(
            installation.payload_root,
            reference,
            "plugin installerReadiness",
        )
        contract = _object(_strict_json(contract_path), "installer readiness manifest")
        _keys(
            contract,
            {"schema", "version", "owner", "state", "reason", "modules"},
            "installer readiness manifest",
        )
        if contract.get("schema") != CONTRACT_SCHEMA:
            raise _ContractProblem(
                f"expected {CONTRACT_SCHEMA} version {CONTRACT_VERSION}"
            )
        _version(contract.get("version"), CONTRACT_VERSION, "contract version")
        owner = _object(contract.get("owner"), "installer readiness owner")
        _keys(owner, {"plugin"}, "installer readiness owner")
        owner_plugin = _string(owner.get("plugin"), "installer readiness owner.plugin")
        if not _ID.fullmatch(owner_plugin) or owner_plugin != installation.plugin_id:
            raise _ContractProblem("installer readiness owner does not match plugin")
        state = contract.get("state")
        if state not in {"supported", "declined"}:
            raise _ContractProblem("installer readiness state must be supported or declined")
        if state == "declined":
            if "modules" in contract:
                raise _ContractProblem("declined declarations cannot contain modules")
            reason = _string(contract.get("reason"), "declined reason")
            return (
                [],
                Decline(installation, reason, contract_path),
                findings,
                is_machine_gated,
            )
        if "reason" in contract:
            raise _ContractProblem("supported declarations cannot contain a decline reason")
        raw_modules = contract.get("modules")
        if not isinstance(raw_modules, list) or not raw_modules:
            raise _ContractProblem("supported declarations require a non-empty modules array")
        command_data: tuple[dict[str, Path], str] | None = None

        def load_commands() -> tuple[Mapping[str, Path], str]:
            nonlocal command_data
            if command_data is None:
                command_data = _payload_commands(installation.payload_root)
            return command_data

        modules = [
            _module(
                value,
                owner=installation,
                source=contract_path,
                command_loader=load_commands,
            )
            for value in raw_modules
        ]
        return modules, None, findings, is_machine_gated
    except (InstallationContextError, _ContractProblem) as error:
        findings.append(
            _finding(
                "invalid-module-metadata",
                str(error),
                installation.payload_root,
                owner=installation.owner_id,
                remedy="Correct the plugin-owned installer/readiness declaration.",
            )
        )
        return [], None, findings, is_machine_gated


def _validate_graph(modules: Sequence[Module]) -> list[Finding]:
    findings: list[Finding] = []
    by_id: dict[str, Module] = {}
    duplicates: set[str] = set()
    for module in modules:
        if module.qualified_id in by_id:
            duplicates.add(module.qualified_id)
        else:
            by_id[module.qualified_id] = module
    for qualified_id in sorted(duplicates):
        module = by_id[qualified_id]
        findings.append(
            _finding(
                "duplicate-module-id",
                f"multiple modules claim '{qualified_id}'",
                module.source,
                owner=module.owner.owner_id,
                module_id=qualified_id,
                remedy="Give every module one unique id within its installation cell.",
            )
        )

    dependencies: dict[str, tuple[str, ...]] = {}
    for module in modules:
        qualified_dependencies = tuple(
            f"{module.owner.provenance.marketplace_id}::{dependency}"
            for dependency in module.dependencies
        )
        dependencies[module.qualified_id] = qualified_dependencies
        for dependency in qualified_dependencies:
            if dependency == module.qualified_id:
                findings.append(
                    _finding(
                        "self-dependency",
                        "module cannot depend on itself",
                        module.source,
                        owner=module.owner.owner_id,
                        module_id=module.qualified_id,
                        remedy="Remove the module's self-dependency.",
                    )
                )
                continue
            if dependency not in by_id:
                findings.append(
                    _finding(
                        "unknown-dependency",
                        f"module depends on unknown module '{dependency}'",
                        module.source,
                        owner=module.owner.owner_id,
                        module_id=module.qualified_id,
                        remedy=(
                            "Declare the prerequisite in the same installation "
                            "cell or remove the dependency."
                        ),
                    )
                )

    state: dict[str, int] = {}
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()

    def visit(module_id: str) -> None:
        state[module_id] = 1
        stack.append(module_id)
        for dependency in sorted(dependencies.get(module_id, ())):
            if dependency not in by_id:
                continue
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                cycle = (*stack[start:], dependency)
                canonical = min(
                    tuple(cycle[index:-1] + cycle[:index] + (cycle[index],))
                    for index in range(len(cycle) - 1)
                )
                if canonical not in reported:
                    reported.add(canonical)
                    module = by_id[module_id]
                    findings.append(
                        _finding(
                            "dependency-cycle",
                            f"dependency cycle: {' -> '.join(cycle)}",
                            module.source,
                            owner=module.owner.owner_id,
                            module_id=module_id,
                            remedy="Remove at least one edge from the dependency cycle.",
                        )
                    )
        stack.pop()
        state[module_id] = 2

    for module_id in sorted(by_id):
        if state.get(module_id, 0) == 0:
            visit(module_id)
    return findings


def discover_modules(
    installations: Iterable[PluginInstallation],
) -> DiscoveryReport:
    """Read plugin-owned manifests from attributable enabled payloads.

    The function is read-only. Callers must supply roots obtained from a host
    manifest or a validated installation receipt; no cache path or ``PATH``
    lookup is performed.
    """
    findings: list[Finding] = []
    modules: list[Module] = []
    declines: list[Decline] = []
    machine_gated: set[str] = set()
    grouped: dict[str, list[PluginInstallation]] = defaultdict(list)
    for installation in installations:
        invalid: list[str] = []
        if not _ID.fullmatch(installation.plugin_id):
            invalid.append("plugin id")
        provenance = installation.provenance
        if not _MARKETPLACE_ID.fullmatch(provenance.marketplace_id):
            invalid.append("marketplace id")
        if not _FINGERPRINT.fullmatch(provenance.source_fingerprint):
            invalid.append("source fingerprint")
        if not provenance.source_kind or not provenance.source_canonical:
            invalid.append("source identity")
        else:
            readable_name = provenance.marketplace_id.rpartition("--")[0]
            try:
                normalized = normalize_source(
                    {
                        "kind": provenance.source_kind,
                        "canonical": provenance.source_canonical,
                        "ref": provenance.source_ref,
                    },
                    from_receipt=True,
                )
                identity = source_identity(
                    normalized,
                    readable_name,
                )
            except InstallationContextError:
                invalid.append("source identity")
            else:
                if (
                    identity["marketplaceId"] != provenance.marketplace_id
                    or identity["fingerprint"] != provenance.source_fingerprint
                ):
                    invalid.append("marketplace provenance")
        if not installation.payload_root.is_absolute():
            invalid.append("absolute payload root")
        if invalid:
            findings.append(
                _finding(
                    "invalid-installation-owner",
                    f"invalid {', '.join(invalid)}",
                    installation.payload_root,
                    owner=installation.owner_id,
                    remedy="Supply an identity-verified enabled plugin installation.",
                )
            )
            continue
        grouped[installation.owner_id].append(installation)
    for owner_id in sorted(grouped):
        candidates = grouped[owner_id]
        roots = {
            os.path.normcase(str(candidate.payload_root.resolve()))
            for candidate in candidates
        }
        fingerprints = {
            candidate.provenance.source_fingerprint for candidate in candidates
        }
        receipts = {
            os.path.normcase(str(candidate.install_receipt.resolve()))
            for candidate in candidates
            if candidate.install_receipt is not None
        }
        if len(roots) != 1 or len(fingerprints) != 1 or len(receipts) > 1:
            findings.append(
                _finding(
                    "ambiguous-installation-owner",
                    f"enabled installation resolves to multiple payload roots: {sorted(roots)}",
                    owner_id,
                    owner=owner_id,
                    remedy="Repair installation receipts or host plugin-root attribution.",
                )
            )
            continue
        first = candidates[0]
        installation = PluginInstallation(
            plugin_id=first.plugin_id,
            payload_root=first.payload_root,
            provenance=first.provenance,
            scopes=tuple(
                sorted({scope for candidate in candidates for scope in candidate.scopes})
            ),
            install_receipt=first.install_receipt,
        )
        discovered, decline, local_findings, is_machine_gated = _discover_installation(
            installation
        )
        findings.extend(local_findings)
        if is_machine_gated:
            machine_gated.add(owner_id)
        modules.extend(discovered)
        if decline is not None:
            declines.append(decline)
    findings.extend(_validate_graph(modules))
    covered = {module.owner.owner_id for module in modules}
    covered.update(decline.owner.owner_id for decline in declines)
    for owner_id in sorted(machine_gated - covered):
        if not any(finding.owner == owner_id for finding in findings):
            findings.append(
                _finding(
                    "missing-module-metadata",
                    "enabled machine-gated plugin was silently omitted",
                    owner_id,
                    owner=owner_id,
                    remedy="Declare supported modules or an intentional decline.",
                )
            )
    return DiscoveryReport(
        modules=tuple(sorted(modules, key=lambda module: module.qualified_id)),
        declines=tuple(sorted(declines, key=lambda decline: decline.owner.owner_id)),
        findings=tuple(findings),
        machine_gated_owners=tuple(sorted(machine_gated)),
    )


def _settings_group(group: SettingsGroup) -> _SettingsValues:
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, tuple[dict[str, Any], Path, Path]] = {}
    findings: list[Finding] = []
    try:
        layer = SettingsLayer(group.layer)
    except ValueError:
        return _SettingsValues(
            enabled={},
            marketplaces={},
            findings=[
                _finding(
                    "invalid-settings-layer",
                    f"settings layer must be user or project, got {group.layer!r}",
                    group.root,
                )
            ],
        )
    paths = (
        USER_SETTINGS_PATHS
        if layer is SettingsLayer.USER
        else PROJECT_SETTINGS_PATHS
    )
    for parts in paths:
        path = group.root.joinpath(*parts)
        if not path.exists():
            continue
        try:
            data = _object(_strict_json(path), "settings")
        except _ContractProblem as error:
            findings.append(
                _finding(
                    "invalid-settings",
                    str(error),
                    path,
                    remedy="Correct the settings JSON before discovering modules.",
                )
            )
            continue
        raw_enabled = data.get("enabledPlugins", {})
        if not isinstance(raw_enabled, dict):
            findings.append(
                _finding(
                    "invalid-settings",
                    "enabledPlugins must be an object",
                    path,
                )
            )
        else:
            for source, value in raw_enabled.items():
                if not isinstance(source, str) or not isinstance(value, bool):
                    findings.append(
                        _finding(
                            "invalid-settings",
                            "enabledPlugins entries require string keys and booleans",
                            path,
                        )
                    )
                    continue
                enabled[source] = value
        raw_marketplaces = data.get("extraKnownMarketplaces", {})
        if not isinstance(raw_marketplaces, dict):
            findings.append(
                _finding(
                    "invalid-settings",
                    "extraKnownMarketplaces must be an object",
                    path,
                )
            )
        else:
            for key, declaration in raw_marketplaces.items():
                if not isinstance(key, str) or not isinstance(declaration, dict):
                    findings.append(
                        _finding(
                            "invalid-settings",
                            "marketplace entries require string keys and objects",
                            path,
                        )
                    )
                    continue
                marketplaces[key] = (declaration, path, group.root)

    return _SettingsValues(
        enabled={source: (value, group.scope) for source, value in enabled.items()},
        marketplaces=marketplaces,
        findings=findings,
    )


def _enabled_from_settings_groups(
    settings_groups: Iterable[SettingsGroup],
) -> tuple[list[_Enabled], list[Finding]]:
    indexed_groups = list(enumerate(settings_groups))
    layer_order = {SettingsLayer.USER: 0, SettingsLayer.PROJECT: 1}
    try:
        ordered = sorted(
            indexed_groups,
            key=lambda item: (layer_order[SettingsLayer(item[1].layer)], item[0]),
        )
    except ValueError:
        ordered = indexed_groups

    enabled: dict[str, tuple[bool, str]] = {}
    marketplaces: dict[str, tuple[dict[str, Any], Path, Path]] = {}
    findings: list[Finding] = []
    for _index, group in ordered:
        values = _settings_group(group)
        enabled.update(values.enabled)
        marketplaces.update(values.marketplaces)
        findings.extend(values.findings)

    result: list[_Enabled] = []
    for source in sorted(
        name for name, (value, _scope) in enabled.items() if value
    ):
        plugin_id, separator, marketplace_key = source.partition("@")
        if not separator or not _ID.fullmatch(plugin_id) or not marketplace_key:
            findings.append(
                _finding(
                    "invalid-enabled-plugin",
                    f"enabled plugin key is not '<plugin>@<marketplace>': {source}",
                    group.root,
                )
            )
            continue
        declaration = marketplaces.get(marketplace_key)
        if declaration is None:
            findings.append(
                _finding(
                    "missing-marketplace-provenance",
                    f"enabled plugin '{source}' has no marketplace source declaration",
                    group.root,
                    remedy=(
                        "Declare extraKnownMarketplaces for every enabled runtime "
                        "so its installation cell can be selected by provenance."
                    ),
                )
            )
            continue
        value, path, declaration_root = declaration
        descriptor = value.get("source")
        if not isinstance(descriptor, dict):
            findings.append(
                _finding(
                    "invalid-marketplace-provenance",
                    f"marketplace '{marketplace_key}' has no source descriptor",
                    path,
                )
            )
            continue
        try:
            normalized = normalize_source(descriptor, declaration_root)
            identity = source_identity(normalized, marketplace_key)
        except InstallationContextError as error:
            findings.append(
                _finding(
                    "invalid-marketplace-provenance",
                    str(error),
                    path,
                )
            )
            continue
        result.append(
            _Enabled(
                plugin_id=plugin_id,
                marketplace_key=marketplace_key,
                fingerprint=identity["fingerprint"],
                scope=enabled[source][1],
                source=source,
            )
        )
    return result, findings


def installations_from_settings(
    settings_groups: Iterable[SettingsGroup],
    durable_home: str | Path,
) -> tuple[tuple[PluginInstallation, ...], tuple[Finding, ...]]:
    """Join enabled settings to active, validated installation-cell receipts."""
    durable = Path(durable_home)
    findings: list[Finding] = []
    enabled, settings_findings = _enabled_from_settings_groups(settings_groups)
    findings.extend(settings_findings)

    cells_by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    marketplaces = durable / "marketplaces"
    enabled_fingerprints = {item.fingerprint for item in enabled}
    if marketplaces.is_dir():
        try:
            cells = sorted(path for path in marketplaces.iterdir() if path.is_dir())
        except OSError as error:
            findings.append(
                _finding(
                    "installation-registry-indeterminate",
                    str(error),
                    marketplaces,
                    remedy="Restore read access to the installation-cell registry.",
                )
            )
            cells = []
        for cell in cells:
            receipt = cell / "namespace.json"
            if not receipt.is_file():
                continue
            try:
                validated = validate_namespace_receipt(receipt, durable)
            except InstallationContextError as error:
                match = _MARKETPLACE_ID.fullmatch(cell.name)
                suffix = cell.name.rpartition("--")[2] if match else ""
                if suffix and any(
                    fingerprint.removeprefix("sha256:").startswith(suffix)
                    for fingerprint in enabled_fingerprints
                ):
                    findings.append(
                        _finding(
                            "invalid-installation-cell",
                            str(error),
                            receipt,
                            remedy=(
                                "Repair or remove the invalid installation-cell receipt."
                            ),
                        )
                    )
                continue
            if validated["receipt"].get("state") == "active":
                cells_by_fingerprint[validated["identity"]["fingerprint"]].append(
                    validated
                )

    installations: dict[tuple[str, str], PluginInstallation] = {}
    scopes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in enabled:
        cells = cells_by_fingerprint.get(item.fingerprint, [])
        if len(cells) != 1:
            code = (
                "installation-not-found"
                if not cells
                else "ambiguous-installation-owner"
            )
            findings.append(
                _finding(
                    code,
                    (
                        f"enabled plugin '{item.source}' matches "
                        f"{len(cells)} active installation cells"
                    ),
                    item.source,
                    remedy=(
                        "Stamp exactly one active cell for this marketplace "
                        "provenance before planning installers."
                    ),
                )
            )
            continue
        cell = cells[0]
        receipt = (
            Path(cell["cellRoot"])
            / "plugins"
            / item.plugin_id
            / "install.json"
        )
        try:
            validated = validate_context_receipt(
                receipt,
                durable,
                expected_marketplace_id=cell["marketplaceId"],
                expected_plugin_id=item.plugin_id,
                environment={},
            )
        except InstallationContextError as error:
            findings.append(
                _finding(
                    "invalid-installation-owner",
                    str(error),
                    receipt,
                    remedy="Stamp or repair the plugin installation receipt.",
                )
            )
            continue
        if validated["state"] != "active":
            findings.append(
                _finding(
                    "inactive-installation",
                    f"enabled plugin installation is {validated['state']}",
                    receipt,
                    owner=f"{validated['marketplaceId']}::{item.plugin_id}",
                )
            )
            continue
        source = validated["source"]
        provenance = MarketplaceProvenance(
            marketplace_id=validated["marketplaceId"],
            source_fingerprint=validated["sourceFingerprint"],
            source_kind=source["kind"],
            source_canonical=source["canonical"],
            source_ref=source["ref"],
        )
        key = (provenance.marketplace_id, item.plugin_id)
        candidate = PluginInstallation(
            plugin_id=item.plugin_id,
            payload_root=Path(validated["payloadRoot"]),
            provenance=provenance,
            scopes=(),
            install_receipt=Path(validated["installReceipt"]),
        )
        prior = installations.get(key)
        if prior is not None and prior.payload_root.resolve() != candidate.payload_root.resolve():
            findings.append(
                _finding(
                    "ambiguous-installation-owner",
                    "settings resolve one installation identity to multiple payload roots",
                    receipt,
                    owner=candidate.owner_id,
                )
            )
            continue
        installations[key] = candidate
        scopes[key].add(item.scope)

    resolved = tuple(
        PluginInstallation(
            plugin_id=installation.plugin_id,
            payload_root=installation.payload_root,
            provenance=installation.provenance,
            scopes=tuple(sorted(scopes[key])),
            install_receipt=installation.install_receipt,
        )
        for key, installation in sorted(installations.items())
    )
    return resolved, tuple(findings)


def discover_from_settings(
    settings_groups: Iterable[SettingsGroup],
    durable_home: str | Path,
) -> DiscoveryReport:
    """Discover modules by joining enabled settings to installation cells."""
    installations, findings = installations_from_settings(
        settings_groups,
        durable_home,
    )
    report = discover_modules(installations)
    return DiscoveryReport(
        modules=report.modules,
        declines=report.declines,
        findings=tuple(findings) + report.findings,
        machine_gated_owners=report.machine_gated_owners,
    )
