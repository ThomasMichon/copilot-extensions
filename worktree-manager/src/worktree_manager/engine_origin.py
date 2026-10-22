"""Read and refresh a registered native origin without changing its authority."""
from __future__ import annotations

import copy
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from plugin_resolve.execution import (
    execution_environment,
    execution_executable,
    windows_batch_command,
)
from plugin_resolve.inventory import inventory_paths
from plugin_resolve.payload import plugin_manifest
from plugin_resolve.preparation import _failure
from plugin_resolve.reflection import identity
from plugin_resolve.registration import _settings_document, strict_json

_WINDOWS = os.name == "nt"
_GIT_SELECTORS = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}
_IDENTITY_FIELDS = ("sourceFingerprint", "marketplaceId", "pluginId", "source", "payloadRoot")


@dataclass(frozen=True)
class RegisteredOrigin:
    source: str
    copilot_home: Path
    payload: Path
    definition: dict
    provenance: dict


def _plain(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError("Registered origin path must be absolute and ordinary")


def _error(operation: str, source: str, detail: str) -> RuntimeError:
    return _failure(
        operation, source, SimpleNamespace(returncode=1, stderr=detail, stdout=""),
    )


def _registrations(home: Path) -> dict:
    _plain(home)
    _plain(home / "settings.json")
    return _settings_document(home / "settings.json", jsonc_header=True).get(
        "extraKnownMarketplaces", {},
    )


def _provenance(primitive: Any, home: Path, plugin: str, marketplace: str, definition: Any) -> dict:
    identity(f"{plugin}@{marketplace}")
    if not isinstance(definition, dict) or not isinstance(definition.get("source"), dict):
        raise ValueError("Registered source has no acquisition definition")
    descriptor = definition["source"]
    if any(key in descriptor for key in ("canonical", "record", "fingerprint", "sha256", "kind")):
        raise ValueError("Native registration contains receipt-only identity fields")
    kind = descriptor.get("source")
    field = {"github": "repo", "git": "url", "directory": "path", "local": "path"}.get(kind)
    if field is None or not isinstance(descriptor.get(field), str) or not descriptor[field].strip():
        raise ValueError("Registered source has no supported native acquisition location")
    normalized = primitive.normalize_source(descriptor, home)
    resolved = primitive.source_identity(normalized, marketplace)
    payload = home / "installed-plugins" / marketplace / plugin
    _plain(payload)
    return {
        "source": {key: resolved[key] for key in ("kind", "canonical", "ref", "record")},
        "sourceFingerprint": resolved["fingerprint"], "marketplaceId": resolved["marketplaceId"],
        "pluginId": plugin, "payloadRoot": str(payload),
        "locator": {"kind": "installed", "copilotHome": str(home), "marketplaceKey": marketplace},
    }


def _ledger(home: Path, source: str, payload: Path) -> dict | None:
    _plain(home / "config.json")
    entries = strict_json(home / "config.json", jsonc_header=True).get("installedPlugins", [])
    if not isinstance(entries, list):
        raise ValueError("Native installed plugin ledger must be an array")
    wanted_plugin, wanted_marketplace = identity(source)
    matches = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Native installed plugin ledger entry is malformed")
        name, marketplace = entry.get("name"), entry.get("marketplace")
        if not isinstance(name, str) or not isinstance(marketplace, str):
            raise ValueError("Native installed plugin ledger identity is malformed")
        identity(f"{name}@{marketplace}")
        if (name, marketplace) != (wanted_plugin, wanted_marketplace):
            continue
        location = entry.get("cache_path")
        if location is not None and (
            not isinstance(location, str) or Path(location) != payload
        ):
            raise ValueError("Native ledger names another payload root")
        matches.append(entry)
    if len(matches) > 1:
        raise ValueError("Native installed plugin ledger identity is ambiguous")
    return matches[0] if matches else None


def select_registered_origin(
    primitive: Any, *, copilot_home: Path, plugin_id: str, source_fingerprint: str,
    marketplace_id: str | None = None, payload_hint: Path | None = None,
) -> RegisteredOrigin:
    """Select from actual registered definitions even when payload bytes are absent."""
    home = Path(copilot_home)
    try:
        identity(f"{plugin_id}@validation")
        if payload_hint is not None:
            _plain(Path(payload_hint))
        candidates = []
        for marketplace, definition in _registrations(home).items():
            if not isinstance(marketplace, str):
                raise ValueError("Native marketplace identity is malformed")
            provenance = _provenance(primitive, home, plugin_id, marketplace, definition)
            if provenance["sourceFingerprint"] != source_fingerprint:
                continue
            if marketplace_id is not None and provenance["marketplaceId"] != marketplace_id:
                continue
            payload = Path(provenance["payloadRoot"])
            if payload_hint is not None and payload != payload_hint:
                continue
            source = f"{plugin_id}@{marketplace}"
            _ledger(home, source, payload)
            candidates.append(RegisteredOrigin(
                source, home, payload, copy.deepcopy(definition), copy.deepcopy(provenance),
            ))
        if len(candidates) != 1:
            raise ValueError("Registered source is missing, foreign, or ambiguous")
        return candidates[0]
    except (OSError, ValueError) as error:
        raise _error("Registered origin selection", plugin_id, str(error)) from error


def _native_acquisition(
    argv: list[str], environment: dict[str, str], cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Bound output and keep native descendants in caller-owned containment."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        job = None
        if _WINDOWS:
            process, job = spawn_sync_in_kill_on_close_job(
                argv, require_job=True, allow_breakaway=False,
                env=environment, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, **no_window_kwargs(),
            )
        else:
            process = subprocess.Popen(
                argv, env=environment, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, **no_window_kwargs(),
            )
        try:
            try:
                rc = process.wait(timeout=180)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                if job is None:
                    process.kill()
                    process.wait()
                raise
        finally:
            if job is not None:
                job.terminate_and_wait(5)
                job.close()
                process.wait()
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(
            argv, rc, stdout.read(8192).decode("utf-8", "replace"),
            stderr.read(8192).decode("utf-8", "replace"),
        )


def refresh_registered_origin(
    primitive: Any, origin: RegisteredOrigin, *, environment: dict[str, str],
    copilot_command: tuple[str, ...] = ("copilot",),
) -> dict:
    """Rehydrate at the unchanged registered path, then verify actual provenance."""
    plugin, marketplace = identity(origin.source)

    def revalidate() -> RegisteredOrigin:
        selected = select_registered_origin(
            primitive, copilot_home=origin.copilot_home, plugin_id=plugin,
            source_fingerprint=origin.provenance["sourceFingerprint"],
            marketplace_id=origin.provenance["marketplaceId"], payload_hint=origin.payload,
        )
        if selected.source != origin.source or selected.definition != origin.definition or any(
            selected.provenance[key] != origin.provenance[key] for key in _IDENTITY_FIELDS
        ):
            raise ValueError("Registered source definition or identity changed")
        return selected

    try:
        revalidate()
        registrations = copy.deepcopy(_registrations(origin.copilot_home))
        verb = "update" if _ledger(origin.copilot_home, origin.source, origin.payload) else "install"
        if not copilot_command or any(not isinstance(arg, str) or not arg for arg in copilot_command):
            raise ValueError("Native Copilot command must be nonempty")
        invocation_cwd = Path.cwd()
        child = execution_environment({
            key: value for key, value in environment.items() if key not in _GIT_SELECTORS
        }, invocation_cwd)
        child["COPILOT_HOME"] = str(origin.copilot_home)
        child["COPILOT_AUTO_UPDATE"] = "false"
        actual_home, _cache = inventory_paths(invocation_cwd, child)
        if actual_home != origin.copilot_home:
            raise ValueError("Native inventory home differs from selected origin")
        argv = list(copilot_command)
        if copilot_command == ("copilot",):
            argv[0] = child.get("COPILOT_PATH") or "copilot"
        argv[0] = execution_executable(argv[0], child, invocation_cwd)
        if _WINDOWS and argv[0].lower().endswith((".cmd", ".bat")):
            argv = windows_batch_command(argv, child)
            argv[0] = execution_executable(argv[0], child, invocation_cwd)
        argv.extend(["plugin", verb, origin.source])
        with tempfile.TemporaryDirectory(prefix="engine-origin-") as neutral:
            try:
                result = _native_acquisition(argv, child, Path(neutral))
            except RuntimeError as error:
                raise _error("Native acquisition containment", origin.source, str(error)) from error
        if result.returncode:
            raise _failure("Native origin acquisition", origin.source, result)
        revalidate()
        if _registrations(origin.copilot_home) != registrations:
            raise ValueError("Native acquisition changed marketplace registrations")
        entry = _ledger(origin.copilot_home, origin.source, origin.payload)
        if entry is None:
            raise ValueError("Native acquisition did not register the requested plugin")
        _plain(origin.payload / "plugin.json")
        manifest = plugin_manifest(origin.payload)
        if (
            manifest.get("name") != plugin
            or manifest.get("marketplace", marketplace) != marketplace
            or not isinstance(manifest.get("version"), str) or not manifest["version"].strip()
        ):
            raise ValueError("Native acquisition produced no valid requested plugin manifest")
        if entry.get("version") is not None and entry["version"] != manifest["version"]:
            raise ValueError("Native ledger and materialized plugin versions differ")
        installer = origin.payload / "scripts" / ("install.ps1" if _WINDOWS else "install.sh")
        _plain(installer)
        if not installer.is_file():
            raise ValueError("Native acquisition produced no ordinary native installer")
        query = dict(environment)
        query.pop("COPILOT_PLUGIN_ROOT", None)
        actual = primitive.resolve_payload_source(
            payload_root=origin.payload, plugin_id=plugin,
            copilot_home=origin.copilot_home, environment=query,
        )
        if any(actual[key] != origin.provenance[key] for key in _IDENTITY_FIELDS):
            raise ValueError("Acquired payload provenance differs from selected origin")
        return actual
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise _error("Registered origin refresh", origin.source, str(error)) from error
