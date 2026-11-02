"""Adapt declared account prerequisites to the existing resource pipeline."""

from __future__ import annotations

from typing import Any

from . import gh_account_prerequisite as prerequisite
from .resources import (
    ResolvedResource,
    ResourceContext,
    ResourceContribution,
    ResourceFinding,
    ResourceHandler,
    ResourceResult,
    _select_field,
    default_runner,
)


class GhAccountResourceHandler(ResourceHandler):
    TYPE = "gh-account"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return (self.TYPE, decl["host"].casefold(), decl["login"].casefold())

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id") or "/".join(self.identity(decl)[1:]))

    def merge(
        self,
        members: list[ResourceContribution],
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        ident = self.identity(members[0].declaration)
        desired = {"host": ident[1], "login": ident[2]}
        value, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "credential_source",
            lambda member: member.declaration["credential_source"],
        )
        findings, decisions = [], []
        if conflict:
            findings.append(
                ResourceFinding(
                    "error",
                    "resource-conflict",
                    f"gh-account '{ident[1]}/{ident[2]}' has conflicting "
                    "credential_source declarations across packages: "
                    + ", ".join(sorted(member.owner for member in selected)),
                )
            )
        if decision:
            decisions.append(decision)
            findings.append(info)
        desired["credential_source"] = value
        return desired, findings, decisions

    def maintenance_safe_decision(
        self,
        resolved: ResolvedResource,
        ctx: ResourceContext,
    ) -> tuple[bool, str | None]:
        return True, None

    def apply(
        self,
        resolved: ResolvedResource,
        ctx: ResourceContext,
        dry_run: bool,
    ) -> ResourceResult:
        desired = resolved.desired
        reason, argv = prerequisite.check_gh_account(
            desired["host"],
            desired["login"],
            desired["credential_source"],
            runner=None if ctx.runner is default_runner else ctx.runner,
        )
        detail = "Declared account prerequisite is satisfied (read-only)."
        if reason is not None:
            detail = (
                f"Account prerequisite is not satisfied ({reason}). "
                f"Inspect gh auth status --hostname {desired['host']} --no-refresh; "
                "credential provisioning and account selection remain manual."
            )
        return ResourceResult(
            self.TYPE,
            resolved.id,
            False,
            dry_run,
            "check",
            detail=detail,
            blocked_reason=reason,
            commands=[argv],
        )
