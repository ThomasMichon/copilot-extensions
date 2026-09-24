"""Declarative Copilot CLI update-policy resource handler.

The Copilot CLI ships its own built-in self-updater: on Windows it hot-swaps
the WinGet Links binstub (``%LOCALAPPDATA%\\Microsoft\\WinGet\\Links\\
copilot.exe``) directly on launch whenever auto-update is enabled, rotating
the prior binary aside as ``copilot.exe.old-<pid>-<unixms>`` in the same
directory. This happens entirely independent of any package manager: once
the self-updater has touched a winget-installed binary, winget itself can no
longer reconcile it (observed: ``winget install`` refuses with "Unable to
remove Portable package as it has been modified"). That means this state
needs its own resource type rather than ``type: package``.

    resources:
      - type: copilot-cli-update
        auto_update: false          # persists COPILOT_AUTO_UPDATE=false
        pinned_version: "1.0.88"   # optional exact FileVersion pin

Windows only for now (the WinGet Links binstub layout has no equivalent
modeled here for other platforms yet; ``applies_on`` filters accordingly).

``auto_update: false`` persists a ``COPILOT_AUTO_UPDATE`` user environment
variable via the registry (``HKCU\\Environment``), which the CLI itself reads
to skip its own update check on startup. ``auto_update: true`` (or omitting
the field) leaves/removes any override, matching the CLI's own default.

``pinned_version`` converges the installed binstub to an exact
``FileVersionInfo.FileVersion`` by restoring a backup the self-updater
already rotated aside next to the live binary. It never fabricates or
downloads a binary: if no rotated-aside backup matches, the resource reports
``blocked`` (a real precondition this run cannot satisfy) rather than a
false success. If the binstub itself is not found at the resolved location
(e.g. a non-WinGet install), the resource reports ``skipped`` -- this
mechanism simply does not apply there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .resources import (
    REGISTRY_TYPE_MAP,
    ResolvedResource,
    ResourceContext,
    ResourceContribution,
    ResourceFinding,
    ResourceHandler,
    ResourceResult,
    RunOutcome,
    _parse_reg_query,
    _select_field,
    canonical_reg_key,
)

_AUTO_UPDATE_KEY = canonical_reg_key("HKCU:\\Environment")
_AUTO_UPDATE_NAME = "COPILOT_AUTO_UPDATE"
_DEFAULT_LINKS_SUBPATH = ("AppData", "Local", "Microsoft", "WinGet", "Links")
_BINSTUB_NAME = "copilot.exe"
_BACKUP_GLOB = "copilot.exe.old*"


@dataclass
class _StepOutcome:
    changed: bool = False
    detail: str = ""
    commands: list[list[str]] = field(default_factory=list)
    skipped_reason: str | None = None
    blocked_reason: str | None = None
    error: str | None = None


def _links_directory(ctx: ResourceContext, override: str | None) -> Path:
    if override:
        return Path(override)
    return ctx.home.joinpath(*_DEFAULT_LINKS_SUBPATH)


def _file_version(ctx: ResourceContext, path: Path) -> str | None:
    """Read a Windows exe's ``FileVersionInfo.FileVersion`` via pwsh."""
    expr = f"[System.Diagnostics.FileVersionInfo]::GetVersionInfo('{path}').FileVersion"
    try:
        out = ctx.runner(["pwsh", "-NoProfile", "-Command", expr])
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.strip()
    return text or None


class CopilotCliUpdateResourceHandler(ResourceHandler):
    TYPE = "copilot-cli-update"

    def identity(self, decl: dict[str, Any]) -> tuple:
        # Singleton: one Copilot CLI installation per machine, so there is no
        # natural per-instance identity field (unlike e.g. self-update's tier).
        return (self.TYPE,)

    def display_id(self, decl: dict[str, Any]) -> str:
        return self.TYPE

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        return super().applies_on(decl, plat) and plat == "windows"

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        ident = self.identity(members[0].declaration)

        auto_update, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "auto_update",
            lambda member: member.declaration.get("auto_update"),
            include=lambda member: member.declaration.get("auto_update") is not None,
        )
        if conflict:
            findings.append(
                ResourceFinding(
                    "error",
                    "resource-conflict",
                    "copilot-cli-update 'auto_update' is declared with conflicting values "
                    f"across packages: {', '.join(sorted(member.owner for member in selected))}.",
                )
            )
        if decision:
            decisions.append(decision)
            findings.append(info)

        pinned_version, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "pinned_version",
            lambda member: member.declaration.get("pinned_version"),
            include=lambda member: member.declaration.get("pinned_version") is not None,
        )
        if conflict:
            versions = sorted({str(member.declaration.get("pinned_version")) for member in selected})
            findings.append(
                ResourceFinding(
                    "error",
                    "resource-conflict",
                    f"copilot-cli-update 'pinned_version' has conflicting values {versions} "
                    f"across packages: {', '.join(sorted(member.owner for member in selected))}.",
                )
            )
        if decision:
            decisions.append(decision)
            findings.append(info)

        # Advanced/test-only override; not a collision-worthy user field.
        links_directory = next(
            (
                member.declaration.get("links_directory")
                for member in members
                if member.declaration.get("links_directory")
            ),
            None,
        )

        desired = {
            "auto_update": auto_update,
            "pinned_version": pinned_version,
            "links_directory": links_directory,
            "maintenance_safe": any(bool(m.declaration.get("maintenance_safe")) for m in members),
        }
        return desired, findings, decisions

    # ----------------------------------------------------------------- #
    # auto_update sub-step
    # ----------------------------------------------------------------- #
    def _reconcile_auto_update(
        self, desired: bool, ctx: ResourceContext, dry_run: bool
    ) -> _StepOutcome:
        try:
            out = ctx.runner(["reg", "query", _AUTO_UPDATE_KEY, "/v", _AUTO_UPDATE_NAME])
        except (OSError, subprocess.SubprocessError):
            out = RunOutcome(1, "", "")
        live_present = out.returncode == 0
        live_value = _parse_reg_query(out.stdout, _AUTO_UPDATE_NAME)[0] if live_present else None

        if desired:
            # Leave/return to the CLI's own default (enabled): remove any override.
            if not live_present:
                return _StepOutcome(detail="auto_update: already default-enabled")
            cmd = ["reg", "delete", _AUTO_UPDATE_KEY, "/v", _AUTO_UPDATE_NAME, "/f"]
            if dry_run:
                return _StepOutcome(
                    changed=True,
                    detail=f"auto_update: would remove {_AUTO_UPDATE_NAME} override",
                    commands=[cmd],
                )
            result = ctx.runner(cmd)
            if result.returncode != 0:
                return _StepOutcome(
                    commands=[cmd],
                    error=f"`{' '.join(cmd)}` exited {result.returncode}: "
                    f"{(result.stderr or result.stdout).strip()[:200]}",
                )
            return _StepOutcome(
                changed=True,
                detail=f"auto_update: removed {_AUTO_UPDATE_NAME} override",
                commands=[cmd],
            )

        if live_present and (live_value or "").strip().lower() == "false":
            return _StepOutcome(detail="auto_update: already disabled")
        reg_type = REGISTRY_TYPE_MAP.get("String", "REG_SZ")
        cmd = ["reg", "add", _AUTO_UPDATE_KEY, "/v", _AUTO_UPDATE_NAME, "/t", reg_type, "/d", "false", "/f"]
        if dry_run:
            return _StepOutcome(
                changed=True,
                detail=f"auto_update: would set {_AUTO_UPDATE_NAME}=false",
                commands=[cmd],
            )
        result = ctx.runner(cmd)
        if result.returncode != 0:
            return _StepOutcome(
                commands=[cmd],
                error=f"`{' '.join(cmd)}` exited {result.returncode}: "
                f"{(result.stderr or result.stdout).strip()[:200]}",
            )
        return _StepOutcome(
            changed=True,
            detail=f"auto_update: set {_AUTO_UPDATE_NAME}=false",
            commands=[cmd],
        )

    # ----------------------------------------------------------------- #
    # pinned_version sub-step
    # ----------------------------------------------------------------- #
    def _reconcile_pin(
        self,
        desired_version: str,
        links_override: str | None,
        ctx: ResourceContext,
        dry_run: bool,
    ) -> _StepOutcome:
        links_dir = _links_directory(ctx, links_override)
        current_path = links_dir / _BINSTUB_NAME
        if not current_path.is_file():
            return _StepOutcome(
                skipped_reason=f"copilot binstub not found at {current_path}",
                detail=f"pin: binstub not found at {current_path}",
            )

        current_version = _file_version(ctx, current_path)
        if current_version == desired_version:
            return _StepOutcome(detail=f"pin: copilot.exe is already {desired_version}")

        try:
            candidates = sorted(
                (p for p in links_dir.glob(_BACKUP_GLOB) if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
        backup = next(
            (p for p in candidates if _file_version(ctx, p) == desired_version), None
        )
        if backup is None:
            return _StepOutcome(
                blocked_reason=(
                    f"no rotated-aside backup matching version {desired_version} found in "
                    f"{links_dir} (current: {current_version}); cannot fabricate a binary"
                ),
                detail=f"pin: no backup matching {desired_version} in {links_dir}",
            )

        if dry_run:
            return _StepOutcome(
                changed=True,
                detail=(
                    f"pin: would rotate current copilot.exe ({current_version}) aside, then "
                    f"restore {backup.name} as copilot.exe ({desired_version})"
                ),
            )

        rotated_name = f"copilot.exe.old-{os.getpid()}-{int(time.time() * 1000)}"
        rotated_path = links_dir / rotated_name
        try:
            current_path.rename(rotated_path)
            shutil.copy2(backup, current_path)
        except OSError as exc:
            return _StepOutcome(error=f"binstub swap failed: {exc}")

        verified = _file_version(ctx, current_path)
        if verified != desired_version:
            return _StepOutcome(
                error=(
                    f"binstub verification failed: expected {desired_version}, got {verified}. "
                    f"Prior binary rotated aside to {rotated_path}."
                )
            )
        return _StepOutcome(
            changed=True,
            detail=(
                f"pin: restored copilot.exe to {desired_version} from {backup.name} "
                f"(prior {current_version} rotated aside to {rotated_name})"
            ),
        )

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        if shutil.which("reg") is None:
            return ResourceResult(
                self.TYPE, resolved.id, False, dry_run, "skip", skipped_reason="'reg' not on PATH"
            )
        d = resolved.desired
        auto_update = d.get("auto_update")
        pinned_version = d.get("pinned_version")

        outcomes: list[_StepOutcome] = []
        if auto_update is not None:
            outcomes.append(self._reconcile_auto_update(auto_update, ctx, dry_run))
        if pinned_version:
            outcomes.append(
                self._reconcile_pin(pinned_version, d.get("links_directory"), ctx, dry_run)
            )

        commands: list[list[str]] = [c for o in outcomes for c in o.commands]
        detail = "; ".join(o.detail for o in outcomes if o.detail)
        changed = any(o.changed for o in outcomes)

        error = next((o.error for o in outcomes if o.error), None)
        if error:
            return ResourceResult(
                self.TYPE, resolved.id, changed, dry_run, "error", detail=error, commands=commands
            )
        blocked = next((o.blocked_reason for o in outcomes if o.blocked_reason), None)
        if blocked:
            return ResourceResult(
                self.TYPE,
                resolved.id,
                changed,
                dry_run,
                "blocked",
                detail=detail,
                blocked_reason=blocked,
                commands=commands,
            )
        # A skip only wins overall if nothing else in this resource changed --
        # a genuine change from the other declared field still counts as
        # convergence even when its sibling step opted out.
        skipped = next((o.skipped_reason for o in outcomes if o.skipped_reason), None)
        if skipped and not changed:
            return ResourceResult(
                self.TYPE, resolved.id, False, dry_run, "skip", detail=detail, skipped_reason=skipped
            )
        action = "pin" if pinned_version else "write"
        return ResourceResult(
            self.TYPE,
            resolved.id,
            changed,
            dry_run,
            action if changed else "none",
            detail=detail or "up-to-date",
            commands=commands,
        )
