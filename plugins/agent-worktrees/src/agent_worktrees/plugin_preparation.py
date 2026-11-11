"""Explicit missing-payload preparation before owner-module readiness."""

from __future__ import annotations

import contextlib
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

from dropin_registry import ScanAuthority
from plugin_activation import (
    activation_custody, activation_preservation_pending,
    current_declarations, declared_payload_roots, validate_identity,
)
from plugin_resolve import RepoPluginSettings, split_source
from plugin_resolve.payload import catalog_metadata, plugin_manifest
from plugin_resolve.registration import _installation_home, strict_json

from . import (
    _installation_context as context, copilot_plugin_profile as profile,
    reconcile, update_acquisition, update_events,
)

SCHEMA = "copilot-extensions.plugin-payload-preparation"
PROFILE_CAPABILITY = "tool-profile-dependencies-v1"


class PreparationError(ValueError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _selection(project: Path, home: Path, include_tool_profile: bool):
    declared = current_declarations(home=home, project_root=project)
    if declared.authority is ScanAuthority.INDETERMINATE:
        raise PreparationError("Plugin declarations are incomplete or invalid.")
    if not include_tool_profile:
        return declared, {}, frozenset()
    try:
        binding, desired = profile.preparation_profile(project)
    except RuntimeError as error:
        raise PreparationError(str(error)) from error
    enabled = dict(declared.settings.enabled)
    marketplaces = dict(declared.settings.marketplaces)
    bases = dict(declared.marketplace_bases)
    for source, on in desired["enabledPlugins"].items():
        validate_identity(source)
        if source in enabled and enabled[source] is not on:
            raise PreparationError("Tool profile conflicts with an explicit plugin choice.")
        enabled[source] = on
    for name, definition in desired["extraKnownMarketplaces"].items():
        descriptor = definition["source"]
        normalized = context.normalize_source(descriptor, project)
        if name in marketplaces:
            previous = context.normalize_source(marketplaces[name]["source"], bases[name])
            if normalized != previous:
                raise PreparationError("Tool profile conflicts with a declared marketplace source.")
            context._get_declarations(name, home / ".copilot", project)
        marketplaces[name] = definition
        bases[name] = project
    isolated = frozenset(
        source for source, on in desired["enabledPlugins"].items()
        if on and not declared.settings.enabled.get(source, False)
        and marketplaces.get(split_source(source)[1], {}).get("source", {}).get("source")
        in {"github", "git"}
    )
    return replace(
        declared, settings=RepoPluginSettings(enabled=enabled, marketplaces=marketplaces),
        marketplace_bases=bases,
    ), binding, isolated


def _prepared_home(home: Path, source: str, definition: dict) -> Path:
    root = _installation_home(home / ".copilot" / "prepared-payloads", source, definition)
    profile._safe_path((home / ".copilot").resolve(), root)
    return root


def _prepared_root(native_home: Path, source: str) -> Path | None:
    name, marketplace = split_source(source)
    root = native_home / "installed-plugins" / marketplace / name
    profile._safe_path(native_home, root)
    receipt_path = native_home / ".installation-complete.json"
    profile._safe_path(native_home, receipt_path)
    receipt = strict_json(receipt_path)
    if not receipt:
        return None
    catalog, entry = receipt.get("catalog"), receipt.get("entry")
    if (
        receipt.get("source") != source
        or not isinstance(catalog, dict) or catalog.get("name") != marketplace
        or not isinstance(entry, dict) or entry.get("name") != name
    ):
        raise PreparationError("Prepared payload receipt identifies a different source.")
    catalog_metadata(source, plugin_manifest(root), catalog, entry)
    return root


def observe(
    project: Path, *, home: Path | None = None, include_tool_profile: bool = False,
) -> dict:
    """Availability is source-qualified; no acquisition or self-provisioning."""
    home = (home or Path.home()).resolve()
    native_home = os.environ.get("COPILOT_HOME")
    if native_home and Path(native_home).resolve() != (home / ".copilot").resolve():
        raise PreparationError("Native Copilot home differs from the selected inventory owner.")
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise PreparationError("Payload preparation requires an existing project directory.")
    declared, binding, isolated = _selection(project, home, include_tool_profile)
    sources = sorted(declared.settings.enabled_sources())
    if len(sources) > 256:
        raise PreparationError("Enabled plugin selection exceeds its bound.")
    ordinary = replace(declared, settings=RepoPluginSettings(
        enabled={source: on for source, on in declared.settings.enabled.items() if source not in isolated},
        marketplaces=declared.settings.marketplaces,
    ))
    roots = declared_payload_roots(ordinary, home=home)
    active_marketplaces = (
        current_declarations(home=home, project_root=project).settings.marketplaces
        if include_tool_profile else declared.settings.marketplaces
    )
    rows = []
    for source in sources:
        validate_identity(source)
        name, marketplace = split_source(source)
        definition = declared.settings.marketplaces.get(marketplace)
        descriptor = definition.get("source") if isinstance(definition, dict) else None
        if not isinstance(descriptor, dict):
            raise PreparationError("Every enabled plugin requires a declared marketplace source.")
        normalized = context.normalize_source(
            descriptor, declared.marketplace_bases[marketplace],
        )
        native_home = (
            _prepared_home(home, source, definition) if source in isolated else home / ".copilot"
        )
        if source not in isolated and marketplace in active_marketplaces:
            context._get_declarations(marketplace, home / ".copilot", project)
        expected = context.source_identity(normalized, marketplace)["fingerprint"]
        row = {
            "source": source, "sourceFingerprint": expected, "state": "needed",
            "reason": "", "payloadRoot": None,
        }
        if include_tool_profile:
            row["selection"] = (
                "profile-dependency" if source in binding.get("profile", {}).get("enabledPlugins", {})
                else "active"
            )
            row["payloadContext"] = {"copilotHome": str(native_home.resolve())}
        root = _prepared_root(native_home, source) if source in isolated else roots[source]
        if root is not None:
            if normalized.kind != "directory":
                actual = context.resolve_payload_source(
                    payload_root=root, plugin_id=name,
                    copilot_home=native_home,
                    project_root=None if source in isolated else project, environment={},
                )
                if actual["sourceFingerprint"] != expected:
                    raise PreparationError("Available payload does not match its declared source.")
            row.update(
                state="ready", reason="source-qualified-payload", payloadRoot=str(root.resolve()),
            )
        else:
            if normalized.kind not in {"github", "git"}:
                raise PreparationError("Unavailable authored local payload requires source-owner reconciliation.")
            row["reason"] = "missing-remote-payload"
        rows.append(row)
    current = _selection(project, home, include_tool_profile)
    if current != (declared, binding, isolated):
        raise PreparationError("Plugin declarations changed during observation.")
    selection = {
        "projectRoot": str(project),
        "copilotHome": str((home / ".copilot").resolve()),
        "enabled": declared.settings.enabled,
        "marketplaces": declared.settings.marketplaces,
        "bases": {name: str(base) for name, base in declared.marketplace_bases.items()},
    }
    if include_tool_profile:
        selection["toolProfile"] = binding
    pending = activation_preservation_pending(home / ".copilot")
    if include_tool_profile:
        pending = pending or any(
            activation_preservation_pending(_prepared_home(
                home, source, declared.settings.marketplaces[split_source(source)[1]],
            ))
            for source in isolated
        )
    return {
        "schema": SCHEMA, "version": 1, "mode": "observe",
        "capabilities": [PROFILE_CAPABILITY],
        **({"selectionMode": PROFILE_CAPABILITY} if include_tool_profile else {}),
        "selectionDigest": _digest(selection), "planDigest": _digest([selection, rows, pending]),
        "activationPending": pending,
        "payloadsReady": not pending and all(row["state"] == "ready" for row in rows),
        "entries": rows, "outcomes": [],
    }


def prepare(
    project: Path, expected_digest: str, *, home: Path | None = None,
    include_tool_profile: bool = False,
) -> dict:
    """Acquire missing payloads only; readiness/installation stay with their owners."""
    home = (home or Path.home()).resolve()
    project = project.resolve(strict=True)
    initial = observe(project, home=home, include_tool_profile=include_tool_profile)
    if not expected_digest or initial["planDigest"] != expected_digest:
        raise PreparationError("Payload preparation plan changed; inspect it again.")
    missing = {row["source"] for row in initial["entries"] if row["state"] == "needed"}
    outcomes = []
    result = initial

    def revalidate() -> dict:
        latest = observe(project, home=home, include_tool_profile=include_tool_profile)
        if latest["selectionDigest"] != initial["selectionDigest"] or any(
            row["state"] == "needed" and row["source"] not in missing
            for row in latest["entries"]
        ):
            raise PreparationError("Enabled payload selection changed during preparation.")
        return latest

    try:
        if initial["activationPending"]:
            with activation_custody(home / ".copilot"):
                pass
            declared, _, isolated = _selection(project, home, include_tool_profile)
            for source in sorted(isolated):
                with activation_custody(_prepared_home(
                    home, source, declared.settings.marketplaces[split_source(source)[1]],
                )):
                    pass
            result = revalidate()
        for source in sorted(missing):
            result = revalidate()
            if next(row for row in result["entries"] if row["source"] == source)["state"] == "ready":
                continue
            copilot = reconcile.resolve_copilot()
            if not copilot:
                raise PreparationError("Native Copilot acquisition is unavailable.")
            name, marketplace = split_source(source)

            def still_missing():
                latest = revalidate()
                if next(
                    row for row in latest["entries"] if row["source"] == source
                )["state"] != "needed":
                    raise PreparationError("Payload availability changed before acquisition.")

            declared, _, isolated = _selection(project, home, include_tool_profile)
            if source in isolated:
                status = update_acquisition.acquire_prepared(
                    source, declared.settings.marketplaces[marketplace], copilot,
                    cwd=project, staging=home / ".copilot" / "prepared-payloads",
                    revalidate=still_missing,
                )
            else:
                status = update_acquisition.acquire_missing(
                    name, marketplace, copilot, cwd=project, revalidate=still_missing,
                    copilot_home=home / ".copilot",
                )
            outcomes.append({"source": source, "status": status})
            result = revalidate()
            if not status.startswith("OK"):
                result["payloadsReady"] = False
                break
        else:
            result = revalidate()
    except (ValueError, OSError, RuntimeError) as error:
        result = {**result, "payloadsReady": False, "refusal": update_events.bounded(str(error), 400)}
    return {**result, "mode": "apply", "outcomes": outcomes}


def add_parsers(sub) -> None:
    parser = sub.add_parser(
        "prepare-plugin-payloads", allow_abbrev=False,
        help="Inspect or explicitly acquire missing enabled payloads without changing activation",
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-digest")
    parser.add_argument("--include-tool-profile", action="store_true")


def command(args) -> int:
    try:
        if not args.apply and args.expected_plan_digest is not None:
            raise PreparationError("A plan digest is used only with explicit apply.")
        with contextlib.redirect_stdout(sys.stderr):
            result = (
                prepare(args.repo, args.expected_plan_digest, include_tool_profile=args.include_tool_profile)
                if args.apply else observe(args.repo, include_tool_profile=args.include_tool_profile)
            )
        print(json.dumps(result))
        return 2 if args.apply and not result["payloadsReady"] else 0
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({
            "schema": SCHEMA, "version": 1, "payloadsReady": False,
            "refusal": update_events.bounded(str(error), 400),
        }))
        return 1
