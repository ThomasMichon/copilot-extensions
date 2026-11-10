"""Inputs for the existing installed Manager lifecycle witness.

The installation owner supplies the admitted source identity and complete
catalog/manifests for each snapshot. Apply the returned configuration before
registration and authentic suite installation, preserving the witness's other
configuration, deadlines and cleanup. New must use normal selected-Machines
discovery; this module neither supplies an observation nor qualifies execution.

The upgrade case needs a separately admitted supported baseline. The witness
must install it, run the ordinary candidate update, and prove changed installed
selection before exercising New. These inputs are not an update receipt.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import re
from typing import Any


def required_readiness_profile(
    catalog: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    *,
    marketplace_root: str,
    manager_version: str,
    repository_config: dict[str, Any],
) -> dict[str, Any]:
    """Derive a complete declared profile without changing activation policy."""
    marketplace = catalog.get("name")
    entries = catalog.get("plugins")
    if not isinstance(marketplace, str) or not marketplace or not isinstance(entries, list) or not entries:
        raise ValueError("A complete admitted marketplace catalog is required")
    if not isinstance(marketplace_root, str) or not marketplace_root:
        raise ValueError("The admitted marketplace root is required")
    if not isinstance(manager_version, str) or not manager_version:
        raise ValueError("The admitted Manager version is required")
    inventory: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Marketplace entries must be objects")
        name, version, source = (entry.get(key) for key in ("name", "version", "source"))
        if (
            not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name)
            or name in inventory or not isinstance(version, str) or not version
            or not isinstance(source, str)
            or PurePosixPath(source).parts != ("plugins", name)
        ):
            raise ValueError("Marketplace inventory has an invalid or duplicate entry")
        manifest = manifests.get(name)
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != name or manifest.get("version") != version
        ):
            raise ValueError(f"Missing or mismatched admitted manifest: {name}")
        scope = manifest.get("runtimeScope", "none")
        if scope not in {"none", "universal", "machine-gated"}:
            raise ValueError(f"Unsupported runtime scope: {name}")
        inventory[name] = {"version": version, "source": source, "runtime_scope": scope}
    for required in ("agent-worktrees", "agent-machines"):
        if required not in inventory or inventory[required]["runtime_scope"] == "none":
            raise ValueError(f"The profile requires the actual {required} runtime")
    configuration = deepcopy(repository_config)
    configuration["require_application_readiness"] = True
    return {
        "repository_config": configuration,
        "settings": {
            "extraKnownMarketplaces": {
                marketplace: {"source": {"source": "directory", "path": marketplace_root}},
            },
            "enabledPlugins": {f"{name}@{marketplace}": True for name in inventory},
        },
        "inventory": inventory,
        "manager_version": manager_version,
    }


def ordinary_upgrade_inputs(
    baseline_identity: dict[str, Any],
    baseline_profile: dict[str, Any],
    candidate_identity: dict[str, Any],
    candidate_profile: dict[str, Any],
) -> dict[str, Any]:
    """Bind distinct admitted source inputs, never infer an executed upgrade."""
    for identity in (baseline_identity, candidate_identity):
        if any(
            not isinstance(identity.get(key), str)
            or not re.fullmatch(r"[0-9a-f]{40}", identity[key])
            for key in ("source_commit", "candidate_commit", "candidate_tree")
        ):
            raise ValueError("Upgrade inputs require the existing complete source identity")
    if baseline_identity["source_commit"] == candidate_identity["source_commit"]:
        raise ValueError("An unchanged source is not an ordinary upgrade baseline")
    for profile in (baseline_profile, candidate_profile):
        if profile["repository_config"].get("require_application_readiness") is not True:
            raise ValueError("Both lifecycle profiles must require application readiness")
        catalog = profile["inventory"]
        enabled = profile["settings"]["enabledPlugins"]
        marketplaces = profile["settings"]["extraKnownMarketplaces"]
        if len(marketplaces) != 1:
            raise ValueError("The profile must retain its admitted marketplace")
        marketplace = next(iter(marketplaces))
        if (
            set(enabled) != {f"{name}@{marketplace}" for name in catalog}
            or any(value is not True for value in enabled.values())
        ):
            raise ValueError("The complete declared inventory must remain enabled")
        for required in ("agent-worktrees", "agent-machines"):
            if required not in catalog or catalog[required]["runtime_scope"] == "none":
                raise ValueError(f"The profile requires the actual {required} runtime")
    before, after = baseline_profile["inventory"], candidate_profile["inventory"]
    changed = sorted(
        name for name, row in after.items()
        if row["runtime_scope"] != "none"
        and (name not in before or before[name]["version"] != row["version"])
    )
    if baseline_profile["manager_version"] != candidate_profile["manager_version"]:
        changed.append("worktree-manager")
    if not changed:
        raise ValueError("A different source with unchanged runtimes is not the upgrade case")
    return {
        "baseline_identity": deepcopy(baseline_identity),
        "baseline_profile": deepcopy(baseline_profile),
        "candidate_identity": deepcopy(candidate_identity),
        "candidate_profile": deepcopy(candidate_profile),
        "changed_runtimes": changed,
    }
