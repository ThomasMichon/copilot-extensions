"""Strict JSON-contract parsing for one plugin-owned installer/readiness manifest.

Turns a single ``PluginInstallation`` into the ``Module``/``Decline``/finding
objects its own manifest declares. Cross-installation discovery (grouping,
dependency-graph validation) lives in :mod:`installer_readiness.graph`.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from installation_context import InstallationContextError

from .model import (
    ConfigurationEmpty,
    Decline,
    Finding,
    Invocation,
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


def discover_installation(
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
