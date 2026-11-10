"""Read-only source and freshness admission for plugin-triggered runtime updates."""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from .installation_probe import strict_json_object


def runtime_package_version(version: str) -> str:
    """Normalize canonical source/repair slot identities without ordering them."""
    ordinary = re.sub(r"[+.]repair\.[0-9a-f]{32}$", "", version)
    return re.sub(
        r"[+.]source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
        r"(?:\.profile\.[0-9a-f]{16})?$",
        "",
        ordinary,
    )


def _read(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise ValueError(f"{path.name} must be an ordinary file")
    if info.st_size > 1024 * 1024:
        raise ValueError(f"{path.name} exceeds the metadata size limit")
    return path.read_bytes()


def _document(path: Path) -> dict[str, Any] | None:
    content = _read(path)
    if content is None:
        return None
    value = json.loads(content.decode("utf-8-sig"), object_pairs_hook=strict_json_object)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _resolved_selection(payload: Path, root: Path) -> str | None:
    if not any(
        (root / name).exists() for name in ("versions", "current-version", "last-known-good")
    ):
        return None
    from agent_procutil.owned_process import run_caller_owned

    helpers = [
        path
        for name in ("versioned_runtime.py", "_versioned_runtime.py")
        if _read(path := payload / "scripts" / name) is not None
    ]
    if len(helpers) != 1:
        raise ValueError(
            "The owning payload needs exactly one canonical selected-runtime primitive"
        )
    helper = helpers[0]
    try:
        result = run_caller_owned(
            [
                sys.executable,
                "-I",
                "-B",
                str(helper),
                "--root",
                str(root),
                "--json",
                "resolve-python",
            ],
            timeout=15,
            max_stdout_bytes=64 * 1024,
            max_stderr_bytes=64 * 1024,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Canonical selected-runtime lookup timed out") from error
    if result.returncode == 1 and result.stderr.strip() == "no runtime installed":
        return None
    if result.returncode:
        raise ValueError("Canonical selected-runtime lookup failed: " + result.stderr.strip())
    value = json.loads(result.stdout, object_pairs_hook=strict_json_object)
    if not isinstance(value, dict) or not isinstance(value.get("python"), str):
        raise ValueError("Canonical selected-runtime lookup returned invalid metadata")
    interpreter = Path(value["python"])
    if not interpreter.is_absolute():
        raise ValueError("Canonical selected-runtime lookup returned a relative path")
    parts = interpreter.parent.resolve().relative_to((root / "versions").resolve()).parts
    if not parts:
        raise ValueError("Canonical selected-runtime lookup did not name an owned slot")
    return parts[0]


def check_runtime_freshness(
    plugin: str,
    payload: Path,
    root: Path,
    *,
    expected: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Preserve selected sources and reject stale automatic deployment decisions.

    This never grants publication permission or applies to an explicit update.
    Legacy manifests without source-kind metadata retain standalone compatibility;
    present source selections and malformed or lost evidence cannot use that path.
    """
    from packaging.version import Version

    from . import reconcile, update_stage

    snapshot: dict[str, Any] = {}

    def denied(reason: str, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return snapshot, {
            "service": plugin,
            "phase": "runtime",
            "reason": reason,
            "message": message,
        }

    try:
        if expected is not None and not isinstance(expected, dict):
            raise ValueError("The cached runtime freshness snapshot is invalid")
        manifest = _document(root / "deploy-manifest.json")
        selected = _read(root / "current-version")
        source_receipt = _document(payload / "runtime-source.json")
        payload_manifest = _document(payload / "plugin.json")
        running = reconcile._runtime_running_record(plugin, root) or {}
        snapshot = {
            "payload": payload_manifest,
            "payload_fingerprint": update_stage.fingerprint(payload, construction_inputs=True),
            "runtime_source": source_receipt,
            "deployment": manifest,
            "selected": selected.decode("utf-8-sig").strip() if selected is not None else None,
            "running": {
                key: running.get(key) for key in ("version", "runtime_generation", "runtime_slot")
            },
        }
        if expected is not None and snapshot != {
            key: value for key, value in expected.items() if key != "resolved_selection"
        }:
            return denied(
                "runtime-freshness-changed",
                "Runtime source/freshness facts changed; rebuild the launch plan.",
            )
        if manifest is not None and manifest.get("service", plugin) != plugin:
            raise ValueError("The deployment manifest names another runtime")
        snapshot["resolved_selection"] = _resolved_selection(payload, root)
        if expected is not None and snapshot != expected:
            return denied(
                "runtime-freshness-changed",
                "The effective selected runtime changed; rebuild the launch plan.",
            )
        if not payload_manifest or not isinstance(payload_manifest.get("version"), str):
            raise ValueError("The owning payload has no package version")
        payload_version = Version(payload_manifest["version"])
        if manifest is None:
            if selected is not None or snapshot["resolved_selection"] is not None or running:
                raise ValueError("The selected/running runtime has no deployment manifest")
            return snapshot, None
        source = manifest.get("source", {})
        if not isinstance(source, dict):
            raise ValueError("The deployed runtime source is invalid")
        deployed_version = source.get("version") or manifest.get("version")
        if not isinstance(deployed_version, str):
            raise ValueError("The deployed runtime has no package version")
        selections = {snapshot["selected"], snapshot["resolved_selection"]} - {None}
        for selected in selections:
            expected_package = deployed_version
            if "runtime_profile" in manifest:
                profile = manifest["runtime_profile"]
                if not isinstance(profile, str) or not profile:
                    raise ValueError("The selected runtime profile is invalid")
                separator = "." if "+" in deployed_version else "+"
                expected_package += separator + profile
            if Version(runtime_package_version(selected)) != Version(expected_package):
                raise ValueError(
                    "The selected runtime package differs from the deployment manifest"
                )
        if (
            snapshot["selected"] is not None
            and "runtime_version" in manifest
            and snapshot["selected"] != manifest["runtime_version"]
        ):
            raise ValueError("The selected runtime identity differs from the deployment manifest")
        versions = [Version(deployed_version)]
        if running:
            versions.append(Version(runtime_package_version(running["version"])))
        if any(payload_version < version for version in versions):
            return denied(
                "runtime-payload-older",
                "The payload is older than the installed or serving runtime; "
                "use an explicit update to select a downgrade.",
            )
        if "runtime_source" in manifest:
            receipt = manifest["runtime_source"]
            if (
                not isinstance(receipt, dict)
                or receipt.get("schema") != "copilot-extensions.runtime-source"
                or type(receipt.get("version")) is not int
                or receipt["version"] != 1
                or receipt.get("plugin") != plugin
                or receipt.get("package_version") != deployed_version
            ):
                raise ValueError("The deployed runtime source receipt is invalid")
            for selected in selections:
                commit, assembly = receipt.get("source_commit"), receipt.get("assembly_hash")
                if (
                    not isinstance(commit, str)
                    or not isinstance(assembly, str)
                    or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)
                    or not re.fullmatch(r"[0-9a-f]{64}", assembly)
                    or not re.search(
                        r"[+.]source\."
                        + re.escape(commit + "." + assembly[:16])
                        + r"(?:\.profile\.[0-9a-f]{16})?(?:[+.]repair\.[0-9a-f]{32})?$",
                        selected,
                    )
                ):
                    raise ValueError("The selected runtime source differs from its receipt")
            if source_receipt != receipt:
                return denied(
                    "runtime-source-selection-required",
                    "The runtime has an independent source selection; use its explicit "
                    "source update rather than replacing it from this plugin payload.",
                )
        elif source_receipt is not None or any(
            marker in selected for selected in selections for marker in ("+source.", ".source.")
        ):
            raise ValueError("The runtime source selection lacks matching deployment evidence")
        kind = source.get("kind")
        if kind == "local":
            path = source.get("path")
            if (
                not isinstance(path, str)
                or not Path(path).is_absolute()
                or not reconcile._same_path(Path(path), payload)
            ):
                return denied(
                    "runtime-source-selection-required",
                    "The runtime was installed from another local source; "
                    "use an explicit update to change its deployment source.",
                )
        elif kind != "marketplace" and "kind" in source:
            raise ValueError("The deployed runtime source kind is unsupported")
        return snapshot, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return denied(
            "runtime-freshness-unavailable",
            f"Cannot establish runtime source/freshness: {error}. "
            "Use the explicit installation update/recovery flow.",
        )
