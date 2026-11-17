"""Component adapter for optional, explicit Index registration retirement.

The aggregate supplies an already resolved Index payload/context selection,
not its own Manager installation root. Saved report paths never select code.
Other native registration families/platforms remain explicitly unsupported.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

DETECTOR = "index-adopted-legacy-tasks-v1"
REVISION = 1
FAMILIES = ("agent-index", "agent-index-engine")


def capabilities():
    return {
        "component": "agent-index", "family": "registrations",
        "detectors": [{"id": DETECTOR, "revision": REVISION}],
        "platforms": ["windows"], "native_owner": "agent-index",
        "scope": "explicit-selected-cell-and-adopted-retained-inert-legacy-root",
        "apply": "native-owner-custody-and-full-registration-revalidation",
        "unsupported": ["other-components", "systemd-user", "launchd", "unattributed-legacy"],
        "update_prerequisite": False,
    }


def _owner(selection):
    from . import _installation_context as governance

    payload = Path(selection["payload_root"])
    if not payload.is_absolute():
        raise ValueError("Index native owner payload must be absolute")
    governance.validate_context_receipt(
        selection["context"], selection["durable_home"],
        expected_marketplace_id=selection["marketplace_id"],
        expected_plugin_id="agent-index", expected_payload_root=payload,
        environment={},
    )
    path = payload / "scripts" / "historical_registration_owner.py"
    for part in (path, *path.parents):
        info = part.lstat()
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Index native owner may not traverse reparse points")
    if not path.is_file():
        raise ValueError("Index native historical registration owner is unavailable")
    spec = importlib.util.spec_from_file_location("_selected_index_historical_registration_owner", path)
    if spec is None or spec.loader is None:
        raise ValueError("Index native historical registration owner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.DETECTOR != DETECTOR or module.REVISION != REVISION:
        raise ValueError("Index native historical registration owner contract is unsupported")
    return module


def _preserved(reason):
    return [{"detector": DETECTOR, "revision": REVISION, "family": "registrations",
             "resource": name, "status": "preserved", "reason": reason} for name in FAMILIES]


def inspect(root, selection):
    if os.name != "nt":
        return _preserved("unsupported-platform:windows-task-scheduler-only")
    try:
        return _owner(selection).inspect(Path(root), selection)
    except (OSError, ValueError, KeyError) as exc:
        return _preserved(str(exc))


def apply(root, selection, finding):
    resource = finding.get("resource")
    base = {"resource": resource, "status": "preserved"}
    if (finding.get("detector") != DETECTOR or type(finding.get("revision")) is not int
            or finding["revision"] != REVISION or finding.get("status") != "candidate"
            or finding.get("family") != "registrations" or resource not in FAMILIES):
        return {**base, "reason": "unsupported-registration-selection"}
    if os.name != "nt":
        return {**base, "reason": "unsupported-platform:windows-task-scheduler-only"}
    try:
        return _owner(selection).apply(Path(root), selection, finding)
    except (OSError, ValueError, KeyError) as exc:
        return {**base, "reason": str(exc)}
