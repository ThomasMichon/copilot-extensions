"""Repair the validated incumbent engine before Manager orchestration."""
from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from agent_procutil import no_window_kwargs


@dataclass(frozen=True)
class _Installation:
    root: Path
    payload: Path
    identity: dict[str, Any]
    sdk: Path
    context_arguments: dict[str, Any] | None = None
    primitive: ModuleType | None = None


@dataclass(frozen=True)
class _RefreshedSource:
    identity: dict[str, Any]
    payload: Path
    context: str | None = None


@dataclass(frozen=True)
class _LegacyOwnerSelection:
    payload: Path
    binding: dict[str, Any] | None


@dataclass(frozen=True)
class RepairResult:
    exit_code: int
    environment: dict[str, str] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PreparedUpdate:
    arguments: list[str]
    fresh_manager: bool = False
    exit_code: int = 0
    environment: dict[str, str] | None = field(default=None, repr=False)


def prepare_update(rest: list[str]) -> PreparedUpdate:
    """Consume only the private bootstrap prefix before ordinary orchestration."""
    if rest[:1] != ["--repair-engine"]:
        return PreparedUpdate(rest)
    if len(rest) < 2:
        print("Update repair requires its fresh source checkout.", file=sys.stderr)
        return PreparedUpdate(rest, True, 2)
    repaired = _repair_installation(Path(rest[1]))
    return PreparedUpdate(rest[2:], True, repaired.exit_code, repaired.environment)


def _policy_sdk(
    source: Path,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any], ModuleType]:
    from .core_install import install_script

    fresh_script = install_script(source)
    if fresh_script is None:
        raise RuntimeError("Fresh bootstrap engine installer is unavailable")
    sdk = fresh_script.parent.parent
    policy = source / "worktree-manager/scripts/update_entry.py"
    if policy.is_symlink() or not policy.is_file() or policy.resolve() != policy.absolute():
        raise RuntimeError("Fresh bootstrap publication reader is unavailable or indirect")
    helper = runpy.run_path(str(policy))
    api, primitive = helper["_publication_api"](sdk)
    return fresh_script, sdk, helper, api, primitive


def _scope_identity(
    receipt: dict[str, Any], activation: dict[str, Any], environment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "owner": {
            "sourceFingerprint": receipt["sourceFingerprint"], "pluginId": "agent-worktrees",
            "mode": "namespaced", "runtimeRoot": receipt["pluginRoot"],
            "environment": environment,
        },
        "context": receipt["installReceipt"],
        "namespaceGeneration": receipt["namespaceGeneration"],
        "installGeneration": receipt["generation"],
        "activationGeneration": activation["activationGeneration"],
    }


def _legacy_owner_selection(
    helper: dict[str, Any], primitive: ModuleType, root: Path,
) -> _LegacyOwnerSelection:
    path = root / "deploy-manifest.json"
    helper["_ordinary"](path)
    manifest = primitive.read_deployment_manifest(path)
    if (
        not isinstance(manifest, dict) or manifest.get("schema_version") != 3
        or manifest.get("service") != "agent-worktrees"
    ):
        raise RuntimeError("Engine repair has no captured incumbent ownership")
    previous = manifest.get("commandBinding")
    if previous is None:
        source = manifest.get("source")
        locator = source.get("path") if isinstance(source, dict) else None
        if not isinstance(locator, str) or not locator or not Path(locator).is_absolute():
            raise RuntimeError("Engine repair has no captured incumbent ownership")
        payload = Path(locator)
        helper["_ordinary"](payload)
        return _LegacyOwnerSelection(payload, None)
    if (
        not isinstance(previous, dict)
        or not isinstance(previous.get("sourceFingerprint"), str)
        or not isinstance(previous.get("payloadRoot"), str)
    ):
        raise RuntimeError("Engine repair captured source identity is incomplete")
    return _LegacyOwnerSelection(Path(previous["payloadRoot"]), previous)


def _legacy_binding(
    helper: dict[str, Any], api: dict[str, Any], primitive: ModuleType,
    root: Path, environment: dict[str, str],
) -> dict[str, Any]:
    selected = _legacy_owner_selection(helper, primitive, root)
    if selected.binding is not None:
        return selected.binding
    query = dict(environment)
    query.pop("COPILOT_PLUGIN_ROOT", None)
    return api["publish_legacy_binding"](
        primitive, payload_root=selected.payload, plugin_id="agent-worktrees",
        legacy_root=root, environment=query,
    )


def _refresh_source(source: Path, environment: dict[str, str]) -> _RefreshedSource:
    """Validate captured ownership before rehydrating missing or damaged source bytes."""
    from .engine_origin import refresh_registered_origin, select_registered_origin

    _script, _sdk, helper, api, primitive = _policy_sdk(source)
    current_environment, profile = primitive._current_environment(
        environment=environment, os_profile=None, platform=None, wsl_distro=None,
    )
    home = Path(profile)
    copilot_home = Path(environment.get("COPILOT_HOME") or home / ".copilot")
    installed = copilot_home / "installed-plugins"
    context = environment.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    marketplace = None
    previous = None
    if context:
        path = Path(context)
        if not path.is_absolute() or len(path.parents) < 5:
            raise RuntimeError("Engine repair context must name a canonical installation receipt")
        durable = path.parents[4]
        receipt = primitive.validate_context_receipt(
            path, durable, expected_plugin_id="agent-worktrees", environment=environment,
        )
        root = Path(receipt["pluginRoot"])
        activation = primitive._activation_result(
            plugin_root=root, durable_home=durable, marketplace_id=receipt["marketplaceId"],
            plugin_id="agent-worktrees", current_environment=current_environment,
            legacy_root=home / ".agent-worktrees",
        )
        if (
            activation["state"] != "valid" or activation["actualMode"] != "namespaced"
            or activation["runtimeRoot"] != str(root)
            or activation["installGeneration"] != receipt["generation"]
        ):
            raise RuntimeError("Engine repair context has no current local activation")
        fingerprint = receipt["sourceFingerprint"]
        marketplace = receipt["marketplaceId"]
        hint = Path(receipt["payloadRoot"])
        expected = _scope_identity(receipt, activation, current_environment)
    else:
        root = home / ".agent-worktrees"
        attributable = environment.get("AGENT_WORKTREES_PAYLOAD_ROOT", "")
        if attributable and Path(attributable).is_relative_to(installed):
            hint, fingerprint = Path(attributable), None
        else:
            legacy = _legacy_owner_selection(helper, primitive, root)
            previous, hint = legacy.binding, legacy.payload
            fingerprint = previous["sourceFingerprint"] if previous is not None else None
            if previous is None and not hint.is_relative_to(installed):
                query = dict(environment)
                query.pop("COPILOT_PLUGIN_ROOT", None)
                provenance = primitive.resolve_payload_source(
                    payload_root=hint, plugin_id="agent-worktrees", environment=query,
                )
                fingerprint = provenance["sourceFingerprint"]
                marketplace = provenance["marketplaceId"]
    payload_hint = hint if hint.is_relative_to(installed) else None
    origin = select_registered_origin(
        primitive, copilot_home=copilot_home, plugin_id="agent-worktrees",
        source_fingerprint=fingerprint, marketplace_id=marketplace, payload_hint=payload_hint,
    )
    if not context:
        durable = home / ".copilot-extensions"
        cell_root = (
            durable / "marketplaces" / origin.provenance["marketplaceId"]
            / "plugins/agent-worktrees"
        )
        activation = primitive._activation_result(
            plugin_root=cell_root, durable_home=durable,
            marketplace_id=origin.provenance["marketplaceId"], plugin_id="agent-worktrees",
            current_environment=current_environment, legacy_root=root,
        )
        if activation["state"] == "valid" and activation["actualMode"] == "namespaced":
            context = activation["context"]
            receipt = primitive.validate_context_receipt(
                context, durable, expected_plugin_id="agent-worktrees",
                expected_marketplace_id=origin.provenance["marketplaceId"],
                expected_payload_root=origin.payload, environment=environment,
            )
            if receipt["sourceFingerprint"] != origin.provenance["sourceFingerprint"]:
                raise RuntimeError("Automatic engine context belongs to another source")
            expected = _scope_identity(receipt, activation, current_environment)
            root = Path(receipt["pluginRoot"])
        else:
            if activation["state"] not in {"missing", "valid"}:
                raise RuntimeError("Engine repair automatic activation is stale, invalid or foreign")
            if previous is None:
                previous = _legacy_binding(helper, api, primitive, root, environment)
            wanted = api["_legacy_receipt"](
                origin.provenance, {"runtimeRoot": str(root), "environment": current_environment},
            )
            if {key: value for key, value in previous.items() if key != "payloadRoot"} != {
                key: value for key, value in wanted.items() if key != "payloadRoot"
            }:
                raise RuntimeError("Engine repair source, installation or environment identity differs")
            expected = _identity(_Installation(root, origin.payload, wanted, _sdk))
    helper["_ordinary"](root / "bin")
    if (root / "bin").exists() and not (root / "bin").is_dir():
        raise RuntimeError("Engine runtime bin is not an ordinary directory")
    refresh_registered_origin(primitive, origin, environment=environment)
    return _RefreshedSource(expected, origin.payload, context or None)


def _scoped_incumbent(
    sdk: Path, helper: dict[str, Any], primitive: ModuleType,
    environment: dict[str, str], context: str, home: Path, refreshed_payload: Path,
) -> _Installation:
    path = Path(context)
    if not path.is_absolute() or len(path.parents) < 5:
        raise RuntimeError("Engine repair context must name a canonical installation receipt")
    durable = path.parents[4]
    receipt = primitive.validate_context_receipt(
        path, durable, expected_plugin_id="agent-worktrees", environment=environment,
        expected_payload_root=refreshed_payload,
    )
    payload = Path(receipt["payloadRoot"])
    helper["_ordinary"](payload)
    query = dict(environment)
    query.pop("COPILOT_PLUGIN_ROOT", None)
    arguments = {
        "payload_root": payload, "plugin_id": "agent-worktrees",
        "legacy_root": home / ".agent-worktrees", "context": path,
        "durable_home": durable, "environment": query,
    }
    identity, validated = primitive._runtime_publication_identity(arguments)
    root = Path(validated["pluginRoot"])
    helper["_ordinary"](root / "bin")
    if (root / "bin").exists() and not (root / "bin").is_dir():
        raise RuntimeError("Scoped engine runtime bin is not an ordinary directory")
    return _Installation(root, payload, identity, sdk, arguments, primitive)


def _incumbent(
    source: Path, environment: dict[str, str], refreshed_payload: Path,
) -> _Installation:
    """Read existing source/installation ownership with the fresh policy SDK."""
    fresh_script, sdk, helper, api, primitive = _policy_sdk(source)
    _, profile = primitive._current_environment(
        environment=environment, os_profile=None, platform=None, wsl_distro=None,
    )
    home = Path(profile)
    context = environment.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if context:
        return _scoped_incumbent(
            sdk, helper, primitive, environment, context, home, refreshed_payload,
        )
    root = home / ".agent-worktrees"
    helper["_ordinary"](root / "bin")
    if (root / "bin").exists() and not (root / "bin").is_dir():
        raise RuntimeError("Engine runtime bin is not an ordinary directory")
    payload = refreshed_payload
    helper["_ordinary"](payload)
    script = payload / "scripts" / fresh_script.name
    helper["_ordinary"](script)
    if not script.is_file():
        raise RuntimeError("Refreshed engine payload has no supported native installer")
    query = dict(environment)
    query.pop("COPILOT_PLUGIN_ROOT", None)
    receipt = api["publish_legacy_binding"](
        primitive, payload_root=payload, plugin_id="agent-worktrees",
        legacy_root=root, environment=query,
    )
    previous = _legacy_binding(helper, api, primitive, root, environment)
    if {key: value for key, value in previous.items() if key != "payloadRoot"} != {
        key: value for key, value in receipt.items() if key != "payloadRoot"
    }:
        raise RuntimeError("Engine repair current legacy source owner differs")
    if (
        receipt["pluginId"] != "agent-worktrees" or receipt["mode"] != "legacy"
        or Path(receipt["runtimeRoot"]) != root
    ):
        raise RuntimeError("Validated engine publication names another installation")
    return _Installation(root, payload, receipt, sdk)


def _identity(installation: _Installation) -> dict[str, Any]:
    if installation.context_arguments is not None:
        return installation.identity
    return {
        key: installation.identity[key]
        for key in (
            "installationId", "pluginId", "mode", "runtimeRoot", "sourceFingerprint", "environment",
        )
    }


def _wait(command: list[str], environment: dict[str, str], cwd: Path) -> int:
    """Do not kill the admitting lifecycle parent before its native child settles."""
    with subprocess.Popen(
        command, env=environment, cwd=cwd,
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, **no_window_kwargs(),
    ) as process:
        interrupted = False
        while True:
            try:
                rc = process.wait()
                break
            except KeyboardInterrupt:
                interrupted = True
        if interrupted:
            raise KeyboardInterrupt
        return rc


def _invoke(installation: _Installation, action: str, environment: dict[str, str]) -> int:
    """The supported CLI validates ownership, mints a capability, and holds custody."""
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell.exe")
        if host is None:
            raise RuntimeError("PowerShell is required for engine repair")
        native = [
            host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(installation.payload / "scripts/install.ps1"), action,
            "-InstallDir", str(installation.root),
        ]
        if installation.context_arguments is not None:
            native += ["-BootstrapPython", str(Path(
                getattr(sys, "_base_executable", sys.executable),
            ).resolve())]
    else:
        host = shutil.which("bash")
        if host is None:
            raise RuntimeError("Bash is required for engine repair")
        native = [
            host, str(installation.payload / "scripts/install.sh"), action,
            "--install-dir", str(installation.root),
        ]
        if installation.context_arguments is not None:
            native += ["--runtime-publication-python", str(Path(
                getattr(sys, "_base_executable", sys.executable),
            ).resolve())]
    base = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    command = [
        base, "-I", "-B", "-X", "utf8",
        str(installation.sdk / "scripts/command_binding.py"),
        "--payload-root", str(installation.sdk),
        "--source-payload-root", str(installation.payload),
        "--plugin-id", "agent-worktrees", "--legacy-root", str(installation.root),
        "--run-lifecycle", "--managed-deployment", "--", *native,
    ]
    child = dict(environment)
    # These locators name the actual selected executable/build payload, not the
    # unrelated Manager checkout. They grant nothing without CLI admission.
    child["COPILOT_PLUGIN_ROOT"] = str(installation.payload)
    child["COPILOT_PLUGIN_STAGED_FROM"] = str(installation.payload)
    child["PATH"] = str(Path(base).parent) + os.pathsep + child.get("PATH", "")
    if installation.context_arguments is not None:
        if installation.primitive is None:
            raise RuntimeError("Scoped repair has no canonical publication SDK")
        with installation.primitive.runtime_publication_lease(
            **installation.context_arguments,
        ) as patch:
            return _wait(native, {**child, **patch}, installation.payload)
    return _wait(command, child, installation.payload)


def _repair_installation(source: Path) -> RepairResult:
    """Restore only the attributable installation, using its owning source."""
    environment = dict(os.environ)
    try:
        refreshed = _refresh_source(source, environment)
        expected = refreshed.identity
        if refreshed.context is not None:
            environment["COPILOT_EXTENSIONS_CONTEXT"] = refreshed.context
        selected = _incumbent(source, environment, refreshed.payload)
        if _identity(selected) != expected:
            raise RuntimeError("Engine installation/source identity changed during acquisition")
        actions = (
            ("update",) if selected.context_arguments is not None
            or (selected.root / "bin").is_dir() else ("provision", "update")
        )
        for action in actions:
            current = _incumbent(source, environment, refreshed.payload)
            if _identity(current) != expected:
                raise RuntimeError("Engine installation/source identity changed during repair")
            rc = _invoke(current, action, environment)
            if rc:
                return RepairResult(rc)
            if action == "provision" and not (current.root / "bin").is_dir():
                raise RuntimeError("Engine provisioning returned success without its runtime bin")
        return RepairResult(0, environment)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"Engine repair refused: {error}", file=sys.stderr)
        return RepairResult(1)


def repair_engine(source: Path) -> int:
    """The exit-only repair entry for callers that do not continue orchestration."""
    return _repair_installation(source).exit_code
