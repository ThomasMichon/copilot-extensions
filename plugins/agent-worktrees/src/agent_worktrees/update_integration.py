"""One final local-integration pass against the update's selected payload."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from . import installer, output, picker_profiles_cli, update_events

PROJECT_INTEGRATION_CAPABILITY = "project-controller-integration/v1"


def prune_stale_pivots() -> None:
    """Auto-remove Picker pivot manifests that just went stale from *this*
    update (a plugin's manifest changed shape/columns and left an old
    materialized copy shadowing it -- see #3509's investigation).

    Deliberately reuses :func:`prunable_findings`'s existing safe subset
    (``duplicate``/``identity-mismatch``/``missing-target``/``invalid-entry``,
    each only once a live correct manifest for the same plugin is already
    resolvable elsewhere in the registry). A ``legacy-unattributed`` or
    ``not-enabled`` finding is never touched here either -- both still need a
    human to reinstall/re-enable the owning plugin. Best-effort: any failure
    here is a warning, never a reason to fail the update itself."""
    try:
        from .picker_support import pivots as pivot_registry
    except Exception as exc:
        output.warn(f"Pivot prune skipped (import failed): {exc}")
        update_events.record("prune", "pivots", f"pivot prune failed: {exc}", required=False)
        return
    try:
        report = pivot_registry.scan_pivot_registry(materialize=False)
        pruned = pivot_registry.prune_stale_entries(report, apply=True)
    except Exception as exc:
        output.warn(f"Pivot prune skipped: {exc}")
        update_events.record("prune", "pivots", f"pivot prune failed: {exc}", required=False)
        return
    removed = [item for item in pruned if item.get("removed")]
    if removed:
        output.ok(
            f"Pruned {len(removed)} stale Picker pivot manifest(s) "
            "(reopen the Picker to re-materialize from the current "
            "plugin manifest): "
            + ", ".join(str(item.get("entry")) for item in removed)
        )


@contextmanager
def _selected_payload(payload: Path, environment: Mapping[str, str]):
    values = {
        "AGENT_WORKTREES_PAYLOAD_ROOT": str(payload),
        "COPILOT_EXTENSIONS_CONTEXT": environment.get("COPILOT_EXTENSIONS_CONTEXT"),
    }
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_selected(payload: Path, platform_name: str, *, controller_args=None) -> bool:
    from . import reconcile
    from .project_update_controller import _CONTROLLER_OWNS_MANAGER

    try:
        environment, _root = reconcile.runtime_installer_environment("agent-worktrees", payload)
    except ValueError as error:
        output.err(f"Integration scope: {error}")
        update_events.record(
            "runtime_preflight", "agent-worktrees@copilot-extensions",
            f"installation context invalid: {error}",
        )
        return False
    if getattr(controller_args, "_controller_owns_manager", None) is _CONTROLLER_OWNS_MANAGER:
        return finish(payload, platform_name, environment, manager_owned_by_controller=True)
    return finish(payload, platform_name, environment)


def finish(
    payload: Path, platform_name: str, environment: Mapping[str, str],
    *, manager_owned_by_controller: bool = False,
) -> bool:
    """Required local outputs cannot be downgraded to advisory skipped work."""
    from . import manager_launch_cli, manager_recovery

    if type(manager_owned_by_controller) is not bool:
        raise ValueError("Manager ownership must be explicit")

    def verify_adoption(adoption) -> None:
        selected = manager_launch_cli._worktree_manager_root().expanduser()
        if not selected.is_absolute() or adoption.root.resolve() != selected.resolve():
            raise manager_recovery.RecoveryRefused(
                "Manager adoption differs from the selected controller root"
            )
        adoption.verify()

    update_events.enter("binstubs")
    recovery_started = time.monotonic()
    adoption = None
    try:
        # The installer also covers an old running updater during self-upgrade.
        with _selected_payload(payload, environment):
            if manager_owned_by_controller:
                adoption = manager_recovery.adopted_manager()
                if adoption is None:
                    recovery = {"status": "not-adopted"}
                else:
                    verify_adoption(adoption)
                    recovery = {"status": "controller-owned"}
            else:
                recovery = manager_recovery.recover()
        status = {
            "current": "SKIPPED (current)",
            "not-adopted": "SKIPPED (Manager not adopted)",
            "recovered": "OK (Manager recovered)",
            "controller-owned": "SKIPPED (Manager owned by project controller)",
        }.get(recovery["status"])
        if status is None:
            raise ValueError("Manager recovery returned an invalid status")
    except (OSError, ValueError, RuntimeError) as error:
        output.err(f"Adopted Manager recovery failed: {error}")
        update_events.record("binstubs", "manager-recovery", f"failed: {error}", started=recovery_started)
        return False
    update_events.record(
        "binstubs", "manager-recovery", status, started=recovery_started,
    )
    started = time.monotonic()
    current = True
    try:
        with _selected_payload(payload, environment):
            result = installer.reconcile_binstubs()
        if not isinstance(result, dict):
            raise ValueError("Project command reconciliation returned an invalid result")
        blocked = result.get("blocked")
        if blocked is None:
            blocked = sorted(set(result.get("preserved", ())) & set(result.get("registered", ())))
        if blocked:
            raise RuntimeError("Unresolved project command ownership: " + ", ".join(blocked))
    except (OSError, ValueError, RuntimeError) as error:
        current = False
        status = f"project command reconciliation failed: {error}"
        output.err(status)
    else:
        status = "OK"
    update_events.record("binstubs", "project-commands", status, started=started)
    if platform_name == "windows" and recovery["status"] != "not-adopted":
        started = time.monotonic()
        with _selected_payload(payload, environment):
            profiles_current = picker_profiles_cli._refresh_terminal_profiles()
        update_events.record(
            "binstubs", "terminal-profiles", "OK" if profiles_current else "profile refresh failed",
            started=started,
        )
        current = current and profiles_current
    if manager_owned_by_controller and adoption is not None:
        try:
            with _selected_payload(payload, environment):
                verify_adoption(adoption)
        except (OSError, ValueError, RuntimeError) as error:
            output.err(f"Adopted Manager changed during integration: {error}")
            update_events.record("binstubs", "manager-adoption", f"failed: {error}")
            return False
    return current
