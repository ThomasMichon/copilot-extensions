"""Declarative self-update resource handler."""

from __future__ import annotations

from typing import Any

from . import self_update as _self_update
from .resources import (
    ResolvedResource,
    ResourceContext,
    ResourceContribution,
    ResourceFinding,
    ResourceHandler,
    ResourceResult,
    _select_field,
)


class SelfUpdateResourceHandler(ResourceHandler):
    TYPE = "self-update"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return (self.TYPE, str(decl.get("tier")))

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("tier"))

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        # Scheduling is implemented for Windows (Scheduled Tasks) and
        # Linux/WSL (systemd --user timers, see self_update_tasks.py); any
        # other platform has no reconciliation backend, so the resource
        # never resolves there rather than silently opting a package in
        # that can never actually register anything.
        return super().applies_on(decl, plat) and plat in ("windows", "linux", "wsl")

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        ident = self.identity(members[0].declaration)
        state, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "state",
            lambda member: str(member.declaration.get("state", "present")),
        )
        if conflict:
            findings.append(
                ResourceFinding(
                    "error",
                    "resource-conflict",
                    f"self-update tier '{ident[1]}' is declared both present and "
                    f"absent across packages: "
                    f"{', '.join(sorted(member.owner for member in selected))}.",
                )
            )
        if decision:
            decisions.append(decision)
            findings.append(info)
        return {
            "tier": ident[1],
            "state": state,
            "maintenance_safe": any(
                bool(member.declaration.get("maintenance_safe")) for member in members
            ),
        }, findings, decisions

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        desired_present = str(resolved.desired.get("state", "present")) != "absent"
        # Dry-run detail text must name the backend actually in play: Windows
        # uses Scheduled Tasks, Linux/WSL uses a systemd --user timer (review
        # finding -- these details previously hard-coded "Scheduled Task"
        # even when query_task_state() had already switched to the systemd
        # backend for this platform).
        backend_label = (
            "systemd --user timer" if ctx.platform in ("linux", "wsl") else "Scheduled Task"
        )

        def runner(argv: list[str], *, cwd=None, timeout=1800):
            _ = cwd, timeout
            outcome = ctx.runner(argv)
            return _self_update.CommandResult(
                argv=list(argv),
                returncode=outcome.returncode,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
            )

        if dry_run:
            task = _self_update.query_task_state(resolved.id, runner=runner, home=ctx.home)
            if task.unavailable:
                # Mirrors reconcile_scheduled_task()'s own "skipped" outcome
                # for this exact case (self_update_tasks._reconcile_linux_timer)
                # -- no reachable systemd --user manager, so this dry-run must
                # report unavailable rather than crash or claim a spurious
                # install/enable action (review finding).
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    False,
                    True,
                    "none",
                    detail=(
                        "no reachable systemd --user manager on this host; "
                        "self-update scheduling is unavailable here (Windows "
                        "uses Scheduled Tasks; Linux/WSL needs a running "
                        "`systemctl --user` session)"
                    ),
                    skipped_reason="no reachable systemd --user manager on this host",
                )
            if desired_present and not task.present:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "install",
                    detail=f"would register the {backend_label}",
                )
            if desired_present and task.enabled is False:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "install",
                    detail=f"would enable the existing {backend_label}",
                )
            if desired_present and not task.matching:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "install",
                    detail=f"would refresh the {backend_label} definition",
                )
            if (not desired_present) and task.present:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "uninstall",
                    detail=f"would remove the {backend_label}",
                )
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                True,
                "none",
                detail=(
                    f"{backend_label} is already registered"
                    if desired_present
                    else f"{backend_label} is already absent"
                ),
            )

        reconcile = _self_update.reconcile_scheduled_task(
            resolved.id,
            desired_present=desired_present,
            runner=runner,
            home=ctx.home,
        )
        if reconcile.status == "deferred":
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                False,
                "defer",
                detail=reconcile.detail,
                deferred_reason=reconcile.detail,
                commands=reconcile.commands,
            )
        if reconcile.status == "error":
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                False,
                "error",
                detail=reconcile.detail,
            )
        return ResourceResult(
            self.TYPE,
            resolved.id,
            reconcile.changed,
            False,
            ("install" if desired_present else "uninstall") if reconcile.changed else "none",
            detail=reconcile.detail,
        )
