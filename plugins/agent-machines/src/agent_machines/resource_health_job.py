"""Converge an already local-adopted job; a package cannot enroll collection."""

from __future__ import annotations

import os
from pathlib import Path

from .resources import (
    ResourceFinding, ResourceHandler, ResourceResult, _select_field,
)


class HealthJobResourceHandler(ResourceHandler):
    TYPE = "health-job"

    def identity(self, decl):
        return (self.TYPE,)

    def display_id(self, decl):
        return self.TYPE

    def applies_on(self, decl, plat):
        return super().applies_on(decl, plat) and plat == "windows"

    def merge(self, members):
        state, selected, _, conflict, decision, info = _select_field(
            members, (self.TYPE,), "state",
            lambda member: str(member.declaration.get("state", "present")),
        )
        findings = []
        if conflict:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                "Health job is declared both present and absent across packages: "
                + ", ".join(sorted(member.owner for member in selected)),
            ))
        if info:
            findings.append(info)
        return {
            "state": state,
            "maintenance_safe": any(bool(member.declaration.get("maintenance_safe")) for member in members),
        }, findings, [decision] if decision else []

    def apply(self, resolved, ctx, dry_run):
        from .health_enrollment import Enrollment, STATE, select

        try:
            root = Path(os.environ.get("AGENT_RT_ROOT") or (ctx.home / ".agent-machines"))
            try:
                (root / STATE).lstat()
            except FileNotFoundError:
                return ResourceResult(
                    self.TYPE, resolved.id, False, dry_run, "none",
                    detail="local health job: not adopted",
                )
            owner = Enrollment(select(executing=True, for_collection=False))
            if dry_run:
                status = owner.status()
                return ResourceResult(
                    self.TYPE, resolved.id, False, True, "none",
                    detail="local health job: " + ("adopted" if status["adopted"] else "not adopted"),
                )
            result = owner.converge(remove=resolved.desired.get("state") == "absent")
            return ResourceResult(
                self.TYPE, resolved.id, bool(result["changed"]), False,
                "error" if result["status"] in {"unknown", "rolled-back"} else "install" if result["changed"] else "none",
                detail="local health job: " + result["status"],
            )
        except (OSError, ValueError, RuntimeError):
            return ResourceResult(
                self.TYPE, resolved.id, False, dry_run, "error",
                detail="health-job lifecycle unavailable; no new local enrollment adopted",
            )
