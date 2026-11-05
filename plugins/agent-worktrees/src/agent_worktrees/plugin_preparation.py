"""Explicit missing-payload preparation before owner-module readiness."""

from __future__ import annotations

import contextlib
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
from plugin_resolve import split_source

from . import _installation_context as context, reconcile, update_acquisition, update_events

SCHEMA = "copilot-extensions.plugin-payload-preparation"


class PreparationError(ValueError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def observe(project: Path, *, home: Path | None = None) -> dict:
    """Availability is source-qualified; no acquisition or self-provisioning."""
    home = (home or Path.home()).resolve()
    native_home = os.environ.get("COPILOT_HOME")
    if native_home and Path(native_home).resolve() != (home / ".copilot").resolve():
        raise PreparationError("Native Copilot home differs from the selected inventory owner.")
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise PreparationError("Payload preparation requires an existing project directory.")
    declared = current_declarations(home=home, project_root=project)
    if declared.authority is ScanAuthority.INDETERMINATE:
        raise PreparationError("Plugin declarations are incomplete or invalid.")
    sources = sorted(declared.settings.enabled_sources())
    if len(sources) > 256:
        raise PreparationError("Enabled plugin selection exceeds its bound.")
    roots = declared_payload_roots(declared, home=home)
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
        context._get_declarations(marketplace, home / ".copilot", project)
        expected = context.source_identity(normalized, marketplace)["fingerprint"]
        row = {"source": source, "sourceFingerprint": expected, "state": "needed", "reason": ""}
        root = roots[source]
        if root is not None:
            if normalized.kind != "directory":
                actual = context.resolve_payload_source(
                    payload_root=root, plugin_id=name,
                    copilot_home=home / ".copilot", project_root=project, environment={},
                )
                if actual["sourceFingerprint"] != expected:
                    raise PreparationError("Available payload does not match its declared source.")
            row.update(state="ready", reason="source-qualified-payload")
        else:
            if normalized.kind not in {"github", "git"}:
                raise PreparationError("Unavailable authored local payload requires source-owner reconciliation.")
            row["reason"] = "missing-remote-payload"
        rows.append(row)
    current = current_declarations(home=home, project_root=project)
    if current != declared:
        raise PreparationError("Plugin declarations changed during observation.")
    selection = {
        "projectRoot": str(project),
        "copilotHome": str((home / ".copilot").resolve()),
        "enabled": declared.settings.enabled,
        "marketplaces": declared.settings.marketplaces,
        "bases": {name: str(base) for name, base in declared.marketplace_bases.items()},
    }
    pending = activation_preservation_pending(home / ".copilot")
    return {
        "schema": SCHEMA, "version": 1, "mode": "observe",
        "selectionDigest": _digest(selection), "planDigest": _digest([selection, rows, pending]),
        "activationPending": pending,
        "payloadsReady": not pending and all(row["state"] == "ready" for row in rows),
        "entries": rows, "outcomes": [],
    }


def prepare(project: Path, expected_digest: str, *, home: Path | None = None) -> dict:
    """Acquire missing payloads only; readiness/installation stay with their owners."""
    home = (home or Path.home()).resolve()
    project = project.resolve(strict=True)
    initial = observe(project, home=home)
    if not expected_digest or initial["planDigest"] != expected_digest:
        raise PreparationError("Payload preparation plan changed; inspect it again.")
    missing = {row["source"] for row in initial["entries"] if row["state"] == "needed"}
    outcomes = []
    result = initial

    def revalidate() -> dict:
        latest = observe(project, home=home)
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
    except (ValueError, OSError) as error:
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


def command(args) -> int:
    try:
        if not args.apply and args.expected_plan_digest is not None:
            raise PreparationError("A plan digest is used only with explicit apply.")
        with contextlib.redirect_stdout(sys.stderr):
            result = (
                prepare(args.repo, args.expected_plan_digest)
                if args.apply else observe(args.repo)
            )
        print(json.dumps(result))
        return 2 if args.apply and not result["payloadsReady"] else 0
    except (ValueError, OSError) as error:
        print(json.dumps({
            "schema": SCHEMA, "version": 1, "payloadsReady": False,
            "refusal": update_events.bounded(str(error), 400),
        }))
        return 1
