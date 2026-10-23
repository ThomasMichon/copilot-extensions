"""Optional control references derived from an already-selected command binding."""

from __future__ import annotations

from pathlib import Path


def add_to_catalog(catalog: dict) -> None:
    candidate = project_candidate(catalog["binding"])
    if candidate is not None:
        catalog["residentControl"] = candidate


def project_candidate(binding: dict) -> dict | None:
    if not isinstance(binding, dict) or binding.get("status") != "selected":
        return None
    source, installation = binding.get("source"), binding.get("installation")
    if not isinstance(source, dict) or not isinstance(installation, dict):
        return None
    values = {
        "installationId": installation.get("installationId"),
        "sourceFingerprint": source.get("fingerprint"),
        "marketplaceId": source.get("marketplaceId"),
        "pluginId": source.get("pluginId"),
        "runtimeRoot": installation.get("runtimeRoot"),
    }
    if any(not isinstance(v, str) or not v or len(v) > 4096 for v in values.values()):
        return None
    if (
        values["pluginId"] != "agent-worktrees"
        or installation.get("pluginId") != values["pluginId"]
        or installation.get("sourceFingerprint") != values["sourceFingerprint"]
        or installation.get("mode") not in {"legacy", "namespaced"}
    ):
        return None
    root = Path(values["runtimeRoot"])
    if not root.is_absolute() or ".." in root.parts:
        return None
    for key in ("installGeneration", "activationGeneration"):
        if key in installation:
            value = installation[key]
            if type(value) is not int or value < 1:
                return None
            values[key] = value
    if installation["mode"] == "namespaced" and not all(
        key in values for key in ("installGeneration", "activationGeneration")
    ):
        return None
    return {
        "version": 1,
        "state": "candidate",
        **values,
        "activeRoutePath": str(root / "status-monitor-routing" / "active.json"),
        "tokenPath": str(root / "status-monitor-control.token"),
    }
