"""Read-only source and freshness admission for plugin-triggered runtime updates."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from packaging.version import Version

from .installation_probe import strict_json_object


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
        manifest = _document(root / "deploy-manifest.json")
        selected = _read(root / "current-version")
        source_receipt = _document(payload / "runtime-source.json")
        payload_manifest = _document(payload / "plugin.json")
        running = reconcile._runtime_running_record(plugin, root) or {}
        snapshot = {
            "payload": payload_manifest,
            "payload_fingerprint": update_stage.fingerprint(payload),
            "runtime_source": source_receipt,
            "deployment": manifest,
            "selected": selected.decode("utf-8-sig").strip() if selected is not None else None,
            "running": {
                key: running.get(key) for key in ("version", "runtime_generation", "runtime_slot")
            },
        }
        if expected is not None and snapshot != expected:
            return denied(
                "runtime-freshness-changed",
                "Runtime source/freshness facts changed; rebuild the launch plan.",
            )
        if not payload_manifest or not isinstance(payload_manifest.get("version"), str):
            raise ValueError("The owning payload has no package version")
        payload_version = Version(payload_manifest["version"])
        if manifest is None:
            if selected is not None or running:
                raise ValueError("The selected/running runtime has no deployment manifest")
            return snapshot, None
        if manifest.get("service", plugin) != plugin:
            raise ValueError("The deployment manifest names another runtime")
        source = manifest.get("source", {})
        if not isinstance(source, dict):
            raise ValueError("The deployed runtime source is invalid")
        deployed_version = source.get("version") or manifest.get("version")
        if not isinstance(deployed_version, str):
            raise ValueError("The deployed runtime has no package version")
        versions = [Version(deployed_version)]
        if running:
            versions.append(Version(running["version"]))
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
            if source_receipt != receipt:
                return denied(
                    "runtime-source-selection-required",
                    "The runtime has an independent source selection; use its explicit "
                    "source update rather than replacing it from this plugin payload.",
                )
        elif source_receipt is not None or (
            snapshot["selected"]
            and any(marker in snapshot["selected"] for marker in ("+source.", ".source."))
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
