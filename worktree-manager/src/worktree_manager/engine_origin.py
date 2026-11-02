"""Read and refresh a registered native origin without changing its authority."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import OwnedCleanupError, _run_owned, run_caller_owned
from update_telemetry.human import current_operation
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

from .engine_acquisition_budget import DEFAULT_TIMEOUT_S, acquisition_timeout

_WINDOWS = os.name == "nt"
_ACQUISITION_TIMEOUT_S = DEFAULT_TIMEOUT_S
_SHARING_ATTEMPTS = 3
_SHARING_BACKOFF_S = 1.0
_SHARING_ERROR = re.compile(r"\b(?:WinError\s+|os\s+error\s+)(?:32|33)\b", re.IGNORECASE)
_REFUSAL = re.compile(
    r"\b(?:401|403|EACCES|EPERM|EINTEGRITY|unauthori[sz]ed|authentication|authorization|"
    r"policy|checksum|signature|certificate|hash)\b|permission denied|access is denied|"
    r"\b(?:WinError\s+|os\s+error\s+)5\b|\b(?:CERT|ERR_TLS|ERR_SSL)_",
    re.IGNORECASE,
)
_GIT_SELECTORS = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}
_IDENTITY_FIELDS = ("sourceFingerprint", "marketplaceId", "pluginId", "source", "payloadRoot")
_NATIVE_EXEC = (
    "import json,os,sys;r=json.load(sys.stdin);os.chdir(r['cwd']);"
    "os.environ.clear();os.environ.update(r['environment']);os.execv(r['argv'][0],r['argv'])"
)


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
    registrations = {}
    for name in ("settings.json", "settings.local.json"):
        path = home / name
        _plain(path)
        registrations.update(_settings_document(path, jsonc_header=True).get(
            "extraKnownMarketplaces", {},
        ))
    return registrations


def _provenance(primitive: Any, home: Path, plugin: str, marketplace: str, definition: Any) -> dict:
    identity(f"{plugin}@{marketplace}")
    if not isinstance(definition, dict) or not isinstance(definition.get("source"), dict):
        raise ValueError("Registered source has no acquisition definition")
    descriptor = definition["source"]
    if any(key in descriptor for key in ("canonical", "record", "fingerprint", "sha256", "kind")):
        raise ValueError("Native registration contains receipt-only identity fields")
    kind = descriptor.get("source")
    field = {
        "github": "repo", "git": "url", "url": "url", "directory": "path", "local": "path",
    }.get(kind)
    if field is None or not isinstance(descriptor.get(field), str) or not descriptor[field].strip():
        raise ValueError("Registered source has no supported native acquisition location")
    normalized = primitive._get_declarations(marketplace, home, None)[-1]["source"]
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


def _potential_identity(primitive: Any, home: Path, marketplace: str, definition: Any) -> dict | None:
    """Identify candidates only; this never admits incomplete native authority."""
    descriptor = definition.get("source") if isinstance(definition, dict) else None
    if not isinstance(descriptor, dict):
        return None
    kind = descriptor.get("source")
    if kind not in ("github", "git", "url", "directory", "local"):
        return None
    if kind in ("directory", "local") and isinstance(descriptor.get("path"), str):
        # Missing unrelated directories must not block another owner. Use the
        # SDK's receipt normalization only to compare identity; strict admission
        # below still requires the real selected directory and native definition.
        path = Path(descriptor["path"])
        path = primitive.canonical_path(
            path if path.is_absolute() else home / path, must_exist=False,
        )
        descriptor = {
            "kind": "directory", "canonical": "directory:" + str(path),
            "ref": descriptor.get("ref", ""),
        }
        normalized = primitive.normalize_source(descriptor, from_receipt=True)
    else:
        normalized = primitive.normalize_source(descriptor, home)
    return primitive.source_identity(normalized, marketplace)


def select_registered_origin(
    primitive: Any, *, copilot_home: Path, plugin_id: str, source_fingerprint: str | None,
    marketplace_id: str | None = None, payload_hint: Path | None = None,
) -> RegisteredOrigin:
    """Select from actual registered definitions even when payload bytes are absent."""
    home = Path(copilot_home)
    try:
        identity(f"{plugin_id}@validation")
        if source_fingerprint is None and payload_hint is None:
            raise ValueError("Origin selection requires a captured identity or attributable payload")
        if payload_hint is not None:
            _plain(Path(payload_hint))
        candidates = []
        invalid = []
        for marketplace, definition in _registrations(home).items():
            if not isinstance(marketplace, str):
                raise ValueError("Native marketplace identity is malformed")
            if payload_hint is not None and (
                home / "installed-plugins" / marketplace / plugin_id != payload_hint
            ):
                continue
            if marketplace_id is not None and not marketplace_id.startswith(
                primitive._slug(marketplace) + "--"
            ):
                continue
            fenced = payload_hint is not None or marketplace_id is not None
            try:
                potential = _potential_identity(primitive, home, marketplace, definition)
            except (OSError, ValueError) as error:
                if fenced:
                    _provenance(primitive, home, plugin_id, marketplace, definition)
                invalid.append(str(error))
                continue
            if potential is None:
                if fenced:
                    _provenance(primitive, home, plugin_id, marketplace, definition)
                continue
            if source_fingerprint is not None and potential["fingerprint"] != source_fingerprint:
                continue
            if marketplace_id is not None and potential["marketplaceId"] != marketplace_id:
                continue
            provenance = _provenance(primitive, home, plugin_id, marketplace, definition)
            payload = Path(provenance["payloadRoot"])
            source = f"{plugin_id}@{marketplace}"
            _ledger(home, source, payload)
            candidates.append(RegisteredOrigin(
                source, home, payload, copy.deepcopy(definition), copy.deepcopy(provenance),
            ))
        if len(candidates) != 1:
            detail = "; ".join(invalid) if not candidates else ""
            raise ValueError("Registered source is missing, foreign, or ambiguous. " + detail)
        return candidates[0]
    except (OSError, ValueError) as error:
        raise _error("Registered origin selection", plugin_id, str(error)) from error


def _native_acquisition(
    argv: list[str], environment: dict[str, str], cwd: Path, *,
    timeout: float = _ACQUISITION_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Bound output and keep native descendants in caller-owned containment."""
    if not _WINDOWS:
        command = [sys.executable, "-I", "-S", "-B", "-c", _NATIVE_EXEC]
        request = json.dumps({"argv": argv, "environment": environment, "cwd": str(cwd)})
        if sys.platform.startswith("linux"):
            result = run_caller_owned(
                command, timeout=timeout, input_text=request,
                max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
        else:
            # The portable owner pins its session/group leader with WNOWAIT;
            # Linux additionally observes caller lifetime and the retired group.
            result = _run_owned(
                command, timeout=timeout, input_text=request, capture_limits=(65536, 65536),
            )
        return subprocess.CompletedProcess(
            argv, result.returncode, result.stdout[:8192], result.stderr[:8192],
        )
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
        timeout_error = None
        try:
            try:
                rc = process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                if job is None:
                    process.kill()
                    process.wait()
                if isinstance(error, KeyboardInterrupt):
                    raise
                timeout_error = error
        finally:
            if job is not None:
                try:
                    job.terminate_and_wait(5)
                    job.close()
                    process.wait()
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    raise OwnedCleanupError(process, job) from error
        try:
            stdout.seek(0)
            stderr.seek(0)
            captured = (
                stdout.read(8192).decode("utf-8", "replace"),
                stderr.read(8192).decode("utf-8", "replace"),
            )
        except (OSError, ValueError):
            if timeout_error is not None:
                raise timeout_error
            raise
        if timeout_error is not None:
            timeout_error.output, timeout_error.stderr = captured
            raise timeout_error
        return subprocess.CompletedProcess(argv, rc, *captured)


def _sharing_failure(result: subprocess.CompletedProcess[str]) -> bool:
    detail = result.stdout + "\n" + result.stderr
    # A capped diagnostic cannot prove that no refusal followed the captured text.
    return bool(
        _WINDOWS and result.returncode == 1
        and len(result.stdout.encode("utf-8")) < 8192
        and len(result.stderr.encode("utf-8")) < 8192
        and _SHARING_ERROR.search(detail)
        and not _REFUSAL.search(detail)
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
        timeout = acquisition_timeout(environment)
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
        scratch = tempfile.TemporaryDirectory(prefix="engine-origin-")
        retained = False
        try:
            deadline = monotonic() + timeout
            for attempt in range(_SHARING_ATTEMPTS):
                if attempt:
                    revalidate()
                    if _registrations(origin.copilot_home) != registrations:
                        raise ValueError("Native acquisition changed marketplace registrations")
                    if _ledger(origin.copilot_home, origin.source, origin.payload) is None:
                        raise ValueError("Native update ledger changed before retry")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    result = _native_acquisition(
                        argv, child, Path(scratch.name), timeout=remaining,
                    )
                except OwnedCleanupError as error:
                    retained = True
                    scratch._finalizer.detach()
                    error.neutral_directory = Path(scratch.name)
                    raise
                except RuntimeError as error:
                    raise _error(
                        "Native acquisition containment", origin.source, str(error),
                    ) from error
                if (
                    verb != "update" or not _sharing_failure(result)
                    or attempt == _SHARING_ATTEMPTS - 1
                    or deadline - monotonic() <= _SHARING_BACKOFF_S
                ):
                    break
                message = (
                    "Native payload refresh hit transient Windows sharing contention; "
                    f"retrying attempt {attempt + 2}/{_SHARING_ATTEMPTS} "
                    "within the original acquisition budget"
                )
                operation = current_operation()
                if operation is not None:
                    operation.progress(message, state="warning")
                else:
                    print(message, file=sys.stderr)
                sleep(_SHARING_BACKOFF_S)
        finally:
            if not retained:
                scratch.cleanup()
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
    except subprocess.TimeoutExpired as error:
        detail = f"Native acquisition timed out after {error.timeout:g} seconds"
        diagnostics = [str(_error("Registered origin refresh", origin.source, detail))]
        for channel, value in (("stdout", error.stdout), ("stderr", error.stderr)):
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            if isinstance(value, str) and value:
                diagnostic = _failure(
                    channel, origin.source,
                    SimpleNamespace(returncode=1, stdout="", stderr=value),
                )
                diagnostics.append(str(diagnostic)[:190])
        raise RuntimeError("\n".join(diagnostics)) from error
    except (OSError, ValueError) as error:
        raise _error("Registered origin refresh", origin.source, str(error)) from error
