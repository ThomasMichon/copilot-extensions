"""Windows power-setting resource handler."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from .manifest import canonical_power_token, normalize_power_setting_value
from .resources import (
    ResolvedResource,
    ResourceContext,
    ResourceContribution,
    ResourceFinding,
    ResourceHandler,
    ResourceResult,
    RunOutcome,
    _select_field,
)

_TRAILING_HEX_RE = re.compile(r"0x([0-9a-f]+)\s*$", re.IGNORECASE | re.MULTILINE)
_GUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _parse_power_query(out: RunOutcome) -> dict[str, Any]:
    if out.returncode != 0:
        return {"known": False, "ac": None, "dc": None}
    indexes = _TRAILING_HEX_RE.findall(out.stdout)
    if len(indexes) < 2:
        return {"known": False, "ac": None, "dc": None}
    return {
        "known": True,
        "ac": int(indexes[-2], 16),
        "dc": int(indexes[-1], 16),
    }


def _parse_active_scheme(out: RunOutcome) -> str | None:
    if out.returncode != 0:
        return None
    match = _GUID_RE.search(out.stdout)
    return match.group(0).casefold() if match else None


class PowerSettingResourceHandler(ResourceHandler):
    TYPE = "power-setting"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return (
            self.TYPE,
            canonical_power_token(decl.get("scheme", "SCHEME_CURRENT")),
            canonical_power_token(decl.get("subgroup")),
            canonical_power_token(decl.get("setting")),
        )

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id") or f"{decl.get('subgroup')}/{decl.get('setting')}")

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        return super().applies_on(decl, plat) and plat == "windows"

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        decls = [member.declaration for member in members]
        _, scheme, subgroup, setting = self.identity(decls[0])
        label = f"{scheme}/{subgroup}/{setting}"
        ident = self.identity(decls[0])
        desired: dict[str, Any] = {
            "scheme": scheme,
            "subgroup": subgroup,
            "setting": setting,
        }
        for source in ("ac", "dc"):
            value, selected, _, conflict, decision, info = _select_field(
                members,
                ident,
                source,
                lambda member, source=source: normalize_power_setting_value(
                    member.declaration[source]
                ),
                include=lambda member, source=source: source in member.declaration,
            )
            if conflict:
                values = sorted(
                    {
                        normalize_power_setting_value(member.declaration[source])
                        for member in selected
                    }
                )
                findings.append(
                    ResourceFinding(
                        "error",
                        "resource-conflict",
                        f"power setting '{label}' has conflicting {source.upper()} "
                        f"values {values} across packages: "
                        f"{', '.join(sorted(member.owner for member in selected))}.",
                    )
                )
            if decision:
                decisions.append(decision)
                findings.append(info)
            if selected:
                desired[source] = value
        return desired, findings, decisions

    def _detect(self, ctx: ResourceContext, desired: dict[str, Any]) -> dict[str, Any]:
        argv = [
            "powercfg",
            "/QH",
            desired["scheme"],
            desired["subgroup"],
            desired["setting"],
        ]
        try:
            return _parse_power_query(ctx.runner(argv))
        except (OSError, subprocess.SubprocessError):
            return {"known": False, "ac": None, "dc": None}

    def _target_is_active(self, ctx: ResourceContext, desired: dict[str, Any]) -> bool | None:
        if desired["scheme"] == "scheme_current":
            return True
        try:
            active = _parse_active_scheme(ctx.runner(["powercfg", "/GETACTIVESCHEME"]))
        except (OSError, subprocess.SubprocessError):
            return None
        return None if active is None else active == desired["scheme"]

    def _rollback(
        self,
        ctx: ResourceContext,
        desired: dict[str, Any],
        live: dict[str, Any],
        sources: list[str],
        target_active: bool,
    ) -> list[str]:
        failures: list[str] = []
        verbs = {"ac": "/SETACVALUEINDEX", "dc": "/SETDCVALUEINDEX"}
        for source in sources:
            argv = [
                "powercfg",
                verbs[source],
                desired["scheme"],
                desired["subgroup"],
                desired["setting"],
                str(live[source]),
            ]
            try:
                outcome = ctx.runner(argv)
            except (OSError, subprocess.SubprocessError):
                failures.append(source.upper())
                continue
            if outcome.returncode != 0:
                failures.append(source.upper())
        if target_active:
            try:
                outcome = ctx.runner(["powercfg", "/SETACTIVE", desired["scheme"]])
            except (OSError, subprocess.SubprocessError):
                failures.append("scheme activation")
            else:
                if outcome.returncode != 0:
                    failures.append("scheme activation")
        return failures

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        d = resolved.desired
        if ctx.platform != "windows":
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                dry_run,
                "skip",
                skipped_reason="power-setting resources apply on Windows only",
            )
        if shutil.which("powercfg") is None:
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                dry_run,
                "skip",
                skipped_reason="'powercfg' not on PATH",
            )

        live = self._detect(ctx, d)
        if not live["known"]:
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                dry_run,
                "error",
                detail=(
                    "could not read the current AC/DC values with "
                    f"`powercfg /QH {d['scheme']} {d['subgroup']} {d['setting']}`"
                ),
            )

        target_active = self._target_is_active(ctx, d)
        if target_active is None:
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                dry_run,
                "error",
                detail="could not determine the active power scheme",
            )

        setter_commands: list[tuple[str, list[str]]] = []
        for source, verb in (("ac", "/SETACVALUEINDEX"), ("dc", "/SETDCVALUEINDEX")):
            if source in d and live[source] != d[source]:
                setter_commands.append(
                    (
                        source,
                        [
                            "powercfg",
                            verb,
                            d["scheme"],
                            d["subgroup"],
                            d["setting"],
                            str(d[source]),
                        ],
                    )
                )
        if not setter_commands:
            return ResourceResult(
                self.TYPE,
                resolved.id,
                False,
                dry_run,
                "none",
                detail="up-to-date",
            )
        commands = [command for _, command in setter_commands]
        if target_active:
            commands.append(["powercfg", "/SETACTIVE", d["scheme"]])

        if dry_run:
            return ResourceResult(
                self.TYPE,
                resolved.id,
                True,
                True,
                "set",
                detail=" ; ".join(" ".join(command) for command in commands),
                commands=commands,
            )

        applied_sources: list[str] = []
        for source, argv in setter_commands:
            out = ctx.runner(argv)
            if out.returncode != 0:
                rollback = ""
                if applied_sources:
                    rollback_failures = self._rollback(
                        ctx, d, live, applied_sources, target_active
                    )
                    rollback = (
                        f"; rollback failed for {', '.join(rollback_failures)}"
                        if rollback_failures
                        else "; prior index writes rolled back"
                    )
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    False,
                    "error",
                    commands=commands,
                    detail=(
                        f"`{' '.join(argv)}` exited {out.returncode}: "
                        f"{(out.stderr or out.stdout).strip()[:200]}{rollback}"
                    ),
                )
            applied_sources.append(source)

        if target_active:
            argv = ["powercfg", "/SETACTIVE", d["scheme"]]
            out = ctx.runner(argv)
            if out.returncode != 0:
                rollback_failures = self._rollback(ctx, d, live, applied_sources, target_active)
                rollback = (
                    f"; rollback failed for {', '.join(rollback_failures)}"
                    if rollback_failures
                    else "; index writes rolled back"
                )
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    True,
                    False,
                    "error",
                    commands=commands,
                    detail=(
                        f"`{' '.join(argv)}` exited {out.returncode}: "
                        f"{(out.stderr or out.stdout).strip()[:200]}{rollback}"
                    ),
                )

        verified = self._detect(ctx, d)
        mismatches = [
            source for source in ("ac", "dc") if source in d and verified.get(source) != d[source]
        ]
        if not verified["known"] or mismatches:
            detail = (
                "post-apply query failed"
                if not verified["known"]
                else f"post-apply values did not match for: {', '.join(mismatches)}"
            )
            return ResourceResult(
                self.TYPE,
                resolved.id,
                True,
                False,
                "error",
                detail=detail,
                commands=commands,
            )
        return ResourceResult(
            self.TYPE,
            resolved.id,
            True,
            False,
            "set",
            detail="applied and verified",
            commands=commands,
        )
