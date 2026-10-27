"""One resolved global-file transaction, with authority selected per Git key."""

from __future__ import annotations

import os
from typing import Any

from . import git_config_transaction as transaction
from .resources import (
    ResolvedResource,
    ResourceContext,
    ResourceContribution,
    ResourceFinding,
    ResourceHandler,
    ResourceResult,
    _select_field,
)


class GitConfigResourceHandler(ResourceHandler):
    TYPE = "git-config"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return self.TYPE, "global"

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id") or "user-global")

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        normalized = {
            id(member): transaction.validate_entries(member.declaration["entries"])
            for member in members
        }
        keys = sorted({key for entries in normalized.values() for key in entries})
        desired, findings, decisions = {}, [], []
        for key in keys:
            value, selected, _, conflict, decision, info = _select_field(
                members,
                (self.TYPE, "global"),
                key,
                lambda member: normalized[id(member)][key],
                include=lambda member: key in normalized[id(member)],
            )
            desired[key] = value
            if conflict:
                findings.append(
                    ResourceFinding(
                        "error",
                        "resource-conflict",
                        f"git-config '{key}' has conflicting declarations across packages: "
                        + ", ".join(sorted(member.owner for member in selected)),
                    )
                )
            if decision:
                decisions.append(decision)
                findings.append(info)
        return {"scope": "global", "entries": desired}, findings, decisions

    def maintenance_safe_decision(
        self, resolved: ResolvedResource, ctx: ResourceContext
    ) -> tuple[bool, str | None]:
        return (
            False,
            "Git configuration changes require an explicit, preview-bound interactive operation",
        )

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        result = ResourceResult(self.TYPE, resolved.id, False, dry_run, "none")
        target = ctx.home / ".gitconfig"
        override = os.environ.get("GIT_CONFIG_GLOBAL")
        if override is not None and (
            not override
            or os.path.normcase(os.path.abspath(override))
            != os.path.normcase(str(target.absolute()))
        ):
            result.action = "blocked"
            result.blocked_reason = "GIT_CONFIG_GLOBAL selects a different file; this resource cannot claim global convergence"
            return result
        home, profile = os.environ.get("HOME"), os.environ.get("USERPROFILE")
        if override is None and ctx.platform == "windows" and home is not None and (
            not home or not profile
            or os.path.normcase(os.path.abspath(home))
            != os.path.normcase(os.path.abspath(profile))
        ):
            result.action = "blocked"
            result.blocked_reason = (
                "HOME and USERPROFILE select different global files; "
                "an explicit GIT_CONFIG_GLOBAL must select the intended target"
            )
            return result
        try:
            change = transaction.preview(target, resolved.desired["entries"])
            result.change_plan = change.summary()
            if not change.changed:
                return result
            result.action = "write"
            if dry_run:
                result.changed = True
                result.detail = (
                    "Preview only. Apply this exact plan with --apply --git-config-plan-sha "
                    + change.plan_sha256
                )
                return result
            receipt = transaction.apply(
                change,
                expected_plan_sha256=ctx.git_config_plan_sha256 or "",
                backup_root=ctx.home / ".agent-machines" / "backups" / "git-config",
            )
            result.changed = True
            result.backup_path = str(receipt)
            result.detail = (
                "Declared global-file keys verified; backup_path names the rollback receipt."
            )
        except transaction.TransactionError as exc:
            result.changed = exc.changed
            result.action = "error" if exc.changed else "blocked"
            result.blocked_reason = str(exc)
            result.detail = str(exc)
            result.backup_path = str(exc.receipt) if exc.receipt is not None else None
        except (OSError, ValueError):
            result.action = "blocked"
            result.blocked_reason = (
                "Git configuration transaction is unavailable; no convergence established"
            )
        return result
