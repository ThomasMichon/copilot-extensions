"""Declarative self-update resource handler."""

from __future__ import annotations

from typing import Any

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
        return {"tier": ident[1], "state": state}, findings, decisions

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        return ResourceResult(
            self.TYPE,
            resolved.id,
            False,
            dry_run,
            "skip",
            skipped_reason=(
                "self-update task reconciliation lands in the installer phase; "
                "this phase records only the opt-in signal"
            ),
        )
