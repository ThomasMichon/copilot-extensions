"""Prerequisite provisioning (Phase 2 — restart-aware, idempotent).

Installer-owned recipes for making missing prerequisites present. Each recipe is
declarative data (per-OS command, whether it changes ``PATH`` so a restart is
needed, and its dependency on another prereq). :func:`plan` turns a set of
detected gaps into an ordered action list; :func:`apply` runs the auto-installable
ones (gated — dry-run by default). "Manual" prereqs (heavy/system, e.g. git) are
surfaced with guidance rather than force-installed.

This is the installer's OWN responsibility — it must work before any plugin — so
these recipes are not a reimplementation of plugin logic; they are the bare-machine
bootstrap the plugins depend on.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from .prereqs import PrereqStatus, current_os

# method: "script" = official installer we run; "uv" = provided by uv once uv is
# present; "manual" = we do not auto-install (print guidance instead).
_RECIPES: dict[str, dict] = {
    "uv": {
        "method": "script",
        "changes_path": True,
        "cmds": {
            "windows": 'powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"',
            "macos": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        },
        "requires": None,
        "note": "Astral uv — user-local install; adds itself to PATH.",
    },
    "python3": {
        # Once uv exists it can provide a managed Python — no system installer.
        "method": "uv",
        "changes_path": True,
        "cmds": {os_: "uv python install" for os_ in ("windows", "macos", "linux")},
        "requires": "uv",
        "note": "Installed via uv's managed Python (uv python install).",
    },
    "git": {
        "method": "manual",
        "changes_path": True,
        "cmds": {
            "windows": "winget install --id Git.Git -e",
            "macos": "brew install git   (or xcode-select --install)",
            "linux": "sudo apt-get install -y git   (or your distro's package manager)",
        },
        "requires": None,
        "note": "System package — install it yourself, then re-run.",
    },
    "psmux": {
        "method": "manual",
        "changes_path": True,
        "cmds": {
            "windows": "see the harness terminal-multiplexer setup (psmux) — optional",
            "macos": "see the harness terminal-multiplexer setup (psmux) — optional",
            "linux": "see the harness terminal-multiplexer setup (psmux) — optional",
        },
        "requires": None,
        "note": "Optional — only needed for interactive multiplexed sessions.",
    },
}


@dataclass(frozen=True)
class ProvisionAction:
    """One planned step to provision a missing prerequisite."""

    name: str
    method: str
    command: str | None
    changes_path: bool
    requires: str | None
    optional: bool
    note: str | None
    #: True for "manual" recipes we won't run — the user must act.
    manual: bool = field(default=False)

    @property
    def auto(self) -> bool:
        return not self.manual and self.command is not None


def recipe_for(name: str, os_: str | None = None) -> ProvisionAction | None:
    """The provisioning action for a prereq on ``os_`` (default: current OS)."""
    r = _RECIPES.get(name)
    if r is None:
        return None
    os_ = os_ or current_os()
    command = r["cmds"].get(os_)
    method = r["method"]
    return ProvisionAction(
        name=name,
        method=method,
        command=command,
        changes_path=bool(r.get("changes_path")),
        requires=r.get("requires"),
        optional=False,
        note=r.get("note"),
        manual=(method == "manual"),
    )


def _order(actions: list[ProvisionAction]) -> list[ProvisionAction]:
    """Topologically order so a prereq's `requires` comes first (e.g. uv before
    python3). Stable; tolerates missing/absent dependencies."""
    by_name = {a.name: a for a in actions}
    ordered: list[ProvisionAction] = []
    seen: set[str] = set()

    def visit(a: ProvisionAction) -> None:
        if a.name in seen:
            return
        seen.add(a.name)
        dep = a.requires
        if dep and dep in by_name:
            visit(by_name[dep])
        ordered.append(a)

    for a in actions:
        visit(a)
    return ordered


def plan(gaps: list[PrereqStatus], os_: str | None = None) -> list[ProvisionAction]:
    """Turn detected prerequisite gaps into an ordered provisioning plan."""
    os_ = os_ or current_os()
    actions: list[ProvisionAction] = []
    for g in gaps:
        base = recipe_for(g.name, os_)
        if base is None:
            actions.append(ProvisionAction(
                name=g.name, method="manual", command=None, changes_path=True,
                requires=None, optional=g.optional,
                note="No recipe — install manually.", manual=True,
            ))
        else:
            actions.append(ProvisionAction(
                name=base.name, method=base.method, command=base.command,
                changes_path=base.changes_path, requires=base.requires,
                optional=g.optional, note=base.note, manual=base.manual,
            ))
    return _order(actions)


@dataclass(frozen=True)
class ProvisionResult:
    action: ProvisionAction
    ran: bool
    returncode: int | None = None
    skipped_reason: str | None = None


def apply(actions: list[ProvisionAction], *, dry_run: bool = True) -> list[ProvisionResult]:
    """Run the auto-installable actions. Dry-run by default — nothing executes
    unless ``dry_run=False``. Manual actions are never executed."""
    results: list[ProvisionResult] = []
    for a in actions:
        if not a.auto:
            results.append(ProvisionResult(a, ran=False, skipped_reason="manual"))
            continue
        if dry_run:
            results.append(ProvisionResult(a, ran=False, skipped_reason="dry-run"))
            continue
        try:
            r = subprocess.run(a.command, shell=True)  # noqa: S602 (installer-owned recipe)
            results.append(ProvisionResult(a, ran=True, returncode=r.returncode))
        except OSError as exc:  # pragma: no cover - environment dependent
            results.append(ProvisionResult(a, ran=False, skipped_reason=str(exc)))
    return results


def restart_needed(results: list[ProvisionResult]) -> bool:
    """True if any action that actually ran changed PATH — the caller should
    prompt the user to restart their shell before continuing."""
    return any(r.ran and r.action.changes_path for r in results)
