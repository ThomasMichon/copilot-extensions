"""Explicit typed scalar-floor adoption through the native settings owner."""

from __future__ import annotations

import argparse
import hmac
import re
from pathlib import Path

from configuration_context import context_from_owner
from plugin_activation import PluginStateError, read_json_object

from . import configuration, preferences as codec, reconcile, validator
from .manifest import ManifestError
from .preference_exchange import _admitted, _distinct, _plain_path, _state
from .surfaces import collect_contributions, settings
from .surfaces._common import copilot_home


def _plan(state: Path, local: Path | None) -> tuple[dict, list, dict, Path]:
    from .__main__ import _collect_reconcile_packages, _resolve_machine_identity

    policy, paths = _state(state)
    if "copilot" not in policy["domains"]:
        raise codec.PreferenceError("domain-not-permitted")
    accepted = codec.read(paths[2])
    if accepted is None:
        raise codec.PreferenceError("accepted-state-required")
    if not _admitted(accepted, policy):
        raise codec.PreferenceError("domain-not-permitted")
    overrides = codec.read(_plain_path(local), local=True) if local is not None else None
    report = configuration.owner_report(Path.cwd())
    if report is None:
        raise codec.PreferenceError("native-context-unavailable")
    context = context_from_owner(report)
    machine, harness = context["execution_machine"], context["repository_id"]
    selectors = argparse.Namespace(all_projects=False, repo=None, machine=machine)
    identity = _resolve_machine_identity(selectors)
    if identity.canonical.casefold() != machine.casefold():
        raise codec.PreferenceError("native-machine-mismatch")
    projection = codec.preview(
        None, accepted, publisher=policy["publisher"], machine=machine, harness=harness, local=overrides,
    )
    floors = projection["values"].get("copilot", {})
    packages, scope = _collect_reconcile_packages(selectors, machine, identity.accepted)
    if not packages:
        raise codec.PreferenceError("native-defaults-unavailable")
    resolved = reconcile.resolve_union(packages, machine, identity.accepted)
    findings = validator.validate(resolved, machine, reconcile.current_platform())
    if validator.has_errors(findings):
        raise codec.PreferenceError("native-validation-failed")
    contributions = [
        (disposition, {key: value for key, value in values.items() if key in configuration.FIELDS}, contributor)
        for disposition, values, contributor in collect_contributions(resolved, "copilot.settings")
        if disposition in {"ensure-present", "enforce"}
    ]
    contributions.append(("ensure-present", floors, "portable-preferences"))
    target = _plain_path(copilot_home() / settings.SETTINGS_FILE, missing_parents=True)
    home = target.parent
    _distinct([*paths, target, *([_plain_path(local)] if local is not None else [])])
    try:
        _, live = read_json_object(target)
        live_digest = codec.digest(live)
    except (PluginStateError, ValueError) as error:
        raise codec.PreferenceError("native-settings-unavailable") from error
    proposed = settings.apply(contributions, home=home, dry_run=True)
    effective = {**live, **{change["key"]: change["after"] for change in proposed.changes}}
    inputs = {
        "state": str(paths[2].parent), "local_path": str(local) if local else None,
        "policy": policy, "accepted": accepted, "local": overrides, "context": context,
        "accepted_machines": identity.accepted,
        "scope": scope, "target": str(target), "live": live_digest,
        "native_packages": reconcile.manifest_hash(resolved), "contributions": contributions,
        "changes": proposed.changes,
    }
    try:
        token = codec.digest(inputs)
    except (TypeError, ValueError) as error:
        raise codec.PreferenceError("native-configuration-unavailable") from error
    return {
        "schema": "agent-machines.preferences.copilot", "version": 1,
        "preview_token": token, "accepted_revision": accepted["revision"],
        "scope": {"machine": machine, "harness_repository_id": harness},
        "floors": floors, "changes": proposed.changes,
        "overridden": sorted(key for key, value in floors.items() if effective.get(key) != value),
        "provenance": {
            key: projection["provenance"][f"copilot.{key}"] for key in floors
        },
        "configuration_applied": False, "binding_changed": False,
        "running_session_delivery": "not-performed",
    }, contributions, live, home


def adopt(state: Path, *, local: Path | None = None, apply=False, expected="") -> dict:
    """Revalidate accepted data, native authority and live state before mutation."""
    if apply and re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise codec.PreferenceError("preview-token-required")
    result, contributions, live, home = _plan(state, local)
    if not apply:
        return result
    if not hmac.compare_digest(expected, result["preview_token"]):
        raise codec.PreferenceError("preview-changed")

    def revalidate(observed: dict) -> None:
        fresh, _, _, _ = _plan(state, local)
        if observed != live or not hmac.compare_digest(expected, fresh["preview_token"]):
            raise codec.PreferenceError("preview-changed")

    try:
        receipt = settings.apply(contributions, home=home, dry_run=False, precondition=revalidate)
    except (PluginStateError, settings.SurfaceStateError, ManifestError) as error:
        raise codec.PreferenceError("native-settings-unavailable") from error
    return {
        **result, "configuration_applied": True, "changed": receipt.changed,
        "changes": receipt.changes, "backup_path": receipt.backup_path,
    }
