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
        return super().applies_on(decl, plat) and plat == "windows"

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
            task = _self_update.query_scheduled_task(resolved.id, runner=runner, home=ctx.home)
            if desired_present and not task.present:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "install",
                    detail="would register the Scheduled Task",
                )
            if desired_present and task.enabled is False:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "install",
                    detail="would enable the existing Scheduled Task",
                )
            if desired_present and not task.matching:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "install",
                    detail="would refresh the Scheduled Task definition",
                )
            if (not desired_present) and task.present:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    True,
                    "uninstall",
                    detail="would remove the Scheduled Task",
                )
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                True,
                "none",
                detail=(
                    "Scheduled Task is already registered"
                    if desired_present
                    else "Scheduled Task is already absent"
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
