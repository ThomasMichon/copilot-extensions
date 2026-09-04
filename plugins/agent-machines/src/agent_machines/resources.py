"""Declarative machine-state resources.

The Copilot ``surfaces`` converge ``~/.copilot/`` config, and ``modules`` are the
repo-local escape hatch for arbitrary OS mutation. **Resources** sit between the
two: typed, identity-bearing declarations of *common* machine state that the
generic engine can converge itself -- so common facts (a package that must be
installed and pinned, a canonical config file) move out of opaque per-repo
scripts and into reviewable data.

A requirement package declares resources under a top-level ``resources:`` list::

    resources:
      - type: package
        id: marlocarlo.psmux        # identity within (type, manager)
        manager: winget             # winget | apt | pipx | uv-tool | pip
        version: "3.3.5"           # exact pin (optional)
        state: present              # present (default) | absent
        pin: true                   # hold at version where the manager supports it
      - type: file
        id: psmux-settings          # display id (optional; defaults to path)
        path: "$HOME/.psmux.conf"  # $HOME / $REPO(<name>) anchored, or absolute
        format: text                # text (default) | json
        strategy: ensure-present    # enforce | ensure-present
        content: |
          set -g mouse on

Five kinds are fully handled: ``package`` (winget/apt/pipx/uv-tool/pip),
``file`` (whole-file *enforce*/*ensure-present* plus a *managed-block* strategy
that owns only a marked block inside an otherwise user-owned file),
``registry`` (Windows registry values, via ``reg.exe``), and ``feature``
(Windows optional features/capabilities via DISM and Linux/WSL units via
``systemctl``, selected by a ``manager`` field), plus ``power-setting`` (Windows
power-scheme AC/DC values via ``powercfg``). Adding a type is a new
``ResourceHandler`` subclass registered in :data:`HANDLERS` -- nothing else in
the engine changes.

**Collision handling is field-local.** When two packages target the same resource
identity -- ``(manager, id)`` for a package; ``(path, block)`` for a file (the
block is empty for whole-file strategies); ``(key, value-name)`` for a registry
value; ``(manager, id)`` for a feature; or ``(scheme, subgroup, setting)`` for
a power setting -- each conflicting semantic field uses its highest-authority
participants. Equal-highest disagreement retains the existing error (or
advisory), while compatible union and safety fields retain every declaration.
Distinct managed blocks in one file are compatible; a whole-file owner and a
block on the same path are not, regardless of authority.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from .authority import contributor, effective_authority, sort_decisions
from .manifest import (
    RequirementPackage,
    canonical_power_token,
    normalize_power_setting_value,
)
from .surfaces._common import backup_file, read_json, write_json_atomic

_ANCHOR_RE = re.compile(r"^\$(HOME|REPO)(?:\(([^)]*)\))?(.*)$")
_SEMVER_RE = re.compile(r"\d+(?:\.\d+)+")
DEFAULT_TIMEOUT = 1800


# --------------------------------------------------------------------------- #
# Findings, results, and the run abstraction
# --------------------------------------------------------------------------- #
@dataclass
class ResourceFinding:
    """A collision/validation result (same shape as ``validator.Finding``)."""

    level: str  # error | advisory | info
    code: str
    message: str


@dataclass
class RunOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str]], RunOutcome]


def default_runner(argv: list[str], timeout: int = DEFAULT_TIMEOUT) -> RunOutcome:
    """Run a package-manager command (argv list, never a shell string)."""
    proc = subprocess.run(  # noqa: S603 - argv list, manager binary resolved via which()
        argv, capture_output=True, encoding="utf-8", errors="replace",
        timeout=timeout, **no_window_kwargs(),
    )
    return RunOutcome(proc.returncode, proc.stdout or "", proc.stderr or "")


@dataclass
class ResolvedResource:
    """One resource identity after cross-package collision resolution."""

    type: str
    id: str  # display id
    identity: tuple
    desired: dict[str, Any]
    contributors: list[str] = field(default_factory=list)
    contributor_details: list[dict[str, Any]] = field(default_factory=list)
    authority_decisions: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        """A one-word disposition-ish label for plan output."""
        if self.type == "package":
            state = self.desired.get("state", "present")
            ver = self.desired.get("version")
            pinned = " pinned" if self.desired.get("pin") else ""
            return f"{state}{('@' + ver) if ver else ''}{pinned}"
        if self.type == "file":
            strategy = str(self.desired.get("strategy", "enforce"))
            if strategy == "managed-block":
                return f"managed-block[{self.desired.get('block', '')}]"
            return strategy
        if self.type == "feature":
            mgr = self.desired.get("manager", "")
            return f"{self.desired.get('state', 'present')} ({mgr})"
        if self.type == "power-setting":
            return " ".join(
                f"{source}={self.desired[source]}"
                for source in ("ac", "dc")
                if source in self.desired
            )
        return self.desired.get("state", "present")


@dataclass
class ResourceResult:
    """The outcome of applying one resolved resource."""

    type: str
    id: str
    changed: bool
    dry_run: bool
    action: str  # install | pin | write | uninstall | none | skip
    detail: str = ""
    skipped_reason: str | None = None
    deferred_reason: str | None = None
    backup_path: str | None = None
    commands: list[list[str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Stable machine-readable outcome independent of display wording."""
        if self.action == "error":
            return "error"
        if self.deferred_reason is not None:
            return "deferred"
        if self.skipped_reason is not None:
            return "skipped"
        return "changed" if self.changed else "ok"

    @property
    def ok(self) -> bool:
        return self.status != "error"


@dataclass
class ResourceContext:
    """Ambient state a handler needs to detect/apply on this machine."""

    home: Path
    repo_paths: dict[str, Path]
    platform: str
    runner: Runner = default_runner


@dataclass(frozen=True)
class ResourceContribution:
    """One package's declaration plus authority and display provenance."""

    package: RequirementPackage
    declaration: dict[str, Any]
    owner: str

    @property
    def authority(self) -> int:
        return effective_authority(self.package, self.declaration)

    @property
    def provenance(self) -> dict[str, Any]:
        return contributor(self.package, self.declaration)

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.owner, self.package.source_repo, self.package.name)


def _semantic_key(value: Any) -> tuple[str, str]:
    return type(value).__name__, repr(value)


def _select_field(
    members: list[ResourceContribution],
    identity: tuple,
    field_name: str,
    value_of,
    *,
    include=lambda _member: True,
) -> tuple[
    Any,
    list[ResourceContribution],
    list[ResourceContribution],
    bool,
    dict[str, Any] | None,
    ResourceFinding | None,
]:
    """Select one semantic field from its highest-authority participants."""
    participants = sorted(
        [member for member in members if include(member)],
        key=lambda member: member.sort_key,
    )
    if not participants:
        return None, [], [], False, None, None
    highest = max(member.authority for member in participants)
    selected = [member for member in participants if member.authority == highest]
    selected_values = {_semantic_key(value_of(member)) for member in selected}
    chosen = value_of(selected[0])
    if len(selected_values) > 1:
        return chosen, selected, [], True, None, None
    chosen_key = _semantic_key(chosen)
    superseded = [
        member
        for member in participants
        if member.authority < highest
        and _semantic_key(value_of(member)) != chosen_key
    ]
    if not superseded:
        return chosen, selected, [], False, None, None
    decision = {
        "domain": "resource",
        "identity": {
            "type": str(identity[0]),
            "key": [str(part) for part in identity[1:]],
            "field": field_name,
        },
        "selected": [member.provenance for member in selected],
        "superseded": [member.provenance for member in superseded],
    }
    label = ":".join(str(part) for part in identity)
    finding = ResourceFinding(
        "info",
        "authority-supersession",
        f"resource '{label}' field '{field_name}' uses authority {highest} from "
        f"{', '.join(member.package.name for member in selected)} over "
        f"{', '.join(member.package.name for member in superseded)}",
    )
    return chosen, selected, superseded, False, decision, finding


# --------------------------------------------------------------------------- #
# Path resolution (file resources)
# --------------------------------------------------------------------------- #
def normalize_path_spec(spec: str) -> str:
    """A stable, cross-platform identity for a file path spec."""
    return spec.replace("\\", "/").rstrip("/")


def resolve_file_path(spec: str, home: Path, repo_paths: dict[str, Path]) -> Path | None:
    """Resolve a ``$HOME``/``$REPO(<name>)``-anchored or literal path to concrete."""
    match = _ANCHOR_RE.match(spec)
    if not match:
        return Path(spec)
    anchor, arg, tail = match.groups()
    tail = (tail or "").lstrip("/\\")
    if anchor == "HOME":
        return home / tail if tail else home
    base = repo_paths.get(arg or "")
    if base is None:
        return None
    return base / tail if tail else base


# --------------------------------------------------------------------------- #
# Managed-block rendering (text file, engine-owned marked block)
# --------------------------------------------------------------------------- #
def _block_present(text: str, begin: str, end: str) -> bool:
    """True when both markers are present as their own lines in ``text``."""
    if not text:
        return False
    lines = text.splitlines()
    return begin in lines and end in lines


def _render_managed_block(current: str, begin: str, end: str, body: str,
                          present: bool) -> str:
    """Return ``current`` with the engine-owned block dropped, then re-appended.

    Preserves all non-block content verbatim, trims trailing blank lines so
    repeated runs never accumulate them, and (when ``present``) re-appends the
    block after a single blank separator. When ``present`` is False the block is
    simply removed. Output always ends in a trailing newline when non-empty.
    """
    kept: list[str] = []
    skip = False
    for line in (current.splitlines() if current else []):
        if line == begin:
            skip = True
            continue
        if line == end:
            skip = False
            continue
        if not skip:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()

    if not present:
        return ("\n".join(kept) + "\n") if kept else ""

    out = list(kept)
    if kept:
        out.append("")  # one blank separator between prior content and the block
    out.append(begin)
    out.extend(body.splitlines())
    out.append(end)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
class ResourceHandler:
    """Base handler: identity, deterministic merge, detect, apply."""

    TYPE = ""

    def identity(self, decl: dict[str, Any]) -> tuple:  # pragma: no cover - abstract
        raise NotImplementedError

    def display_id(self, decl: dict[str, Any]) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        platforms = decl.get("platforms")
        return not platforms or plat in platforms

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[
        dict[str, Any], list[ResourceFinding], list[dict[str, Any]]
    ]:  # pragma: no cover - abstract
        raise NotImplementedError

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:  # pragma: no cover - abstract
        raise NotImplementedError


class PackageResourceHandler(ResourceHandler):
    TYPE = "package"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return ("package", str(decl.get("manager")), str(decl.get("id")))

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id"))

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        if not super().applies_on(decl, plat):
            return False
        mgr = MANAGERS.get(str(decl.get("manager")))
        # An unknown manager still 'applies' (so a missing handler is reported at
        # apply); a known manager filters by the platforms it supports.
        return mgr is None or plat in mgr["platforms"]

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        decls = [member.declaration for member in members]
        ident = self.identity(decls[0])
        state, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "state",
            lambda member: str(member.declaration.get("state", "present")),
        )
        if conflict:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"package '{ident[2]}' ({ident[1]}) is declared both present and "
                f"absent across packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        version, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "version",
            lambda member: str(member.declaration["version"]),
            include=lambda member: bool(member.declaration.get("version")),
        )
        if conflict:
            versions = sorted({
                str(member.declaration["version"]) for member in selected
            })
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"package '{ident[2]}' ({ident[1]}) is pinned to conflicting "
                f"versions {versions} across packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        pin = any(bool(d.get("pin")) for d in decls)
        guard_names = sorted({
            str(name).strip().casefold()
            for d in decls
            for name in (d.get("process_guard") or {}).get("names", [])
        })
        desired = {"manager": ident[1], "id": ident[2], "state": state,
                   "version": version, "pin": pin,
                   "process_guard": {"names": guard_names} if guard_names else None}
        return desired, findings, decisions

    # -- detect / apply -----------------------------------------------------
    def _detect(self, ctx: ResourceContext, mgr: dict, pkg_id: str) -> dict[str, Any]:
        argv = mgr["detect"](pkg_id, None)
        try:
            out = ctx.runner(argv)
        except (OSError, subprocess.SubprocessError):
            return {"known": False, "present": False, "version": None}
        detected = mgr["parse_detect"](out, pkg_id)
        detected["known"] = (
            out.returncode == 0
            or (
                not detected["present"]
                and out.returncode in mgr.get("absent_returncodes", set())
            )
        )
        return detected

    def _detect_pin(
        self, ctx: ResourceContext, mgr: dict, pkg_id: str
    ) -> dict[str, Any]:
        detect_pin = mgr.get("detect_pin")
        parse_pin = mgr.get("parse_pin")
        if not detect_pin or not parse_pin:
            return {"known": True, "present": False, "version": None}
        try:
            out = ctx.runner(detect_pin(pkg_id))
        except (OSError, subprocess.SubprocessError):
            return {"known": False, "present": False, "version": None}
        detected = parse_pin(out, pkg_id)
        detected["known"] = (
            out.returncode == 0
            or (
                not detected["present"]
                and out.returncode in mgr.get("absent_returncodes", set())
            )
        )
        return detected

    @staticmethod
    def _matches_desired(live: dict[str, Any], version: str | None) -> bool:
        return bool(
            live.get("known", True)
            and live["present"]
            and (not version or live.get("version") == version)
        )

    @staticmethod
    def _process_guard(
        ctx: ResourceContext, names: list[str]
    ) -> tuple[bool, list[str], str | None]:
        """Return guard-known, matching process names, and an unknown reason."""
        if not names:
            return True, [], None
        probe = PROCESS_GUARD_PROBES.get(ctx.platform)
        if probe is None:
            return False, [], f"process guards are not supported on '{ctx.platform}'"
        if shutil.which(probe["bin"]) is None:
            return False, [], f"process guard binary '{probe['bin']}' is not on PATH"
        try:
            out = ctx.runner(probe["command"]())
        except (OSError, subprocess.SubprocessError) as exc:
            return False, [], f"process guard probe failed: {exc}"
        if out.returncode != 0:
            detail = (out.stderr or out.stdout).strip()[:200]
            return False, [], (
                f"process guard probe exited {out.returncode}"
                + (f": {detail}" if detail else "")
            )
        running = probe["parse"](out)
        wanted = {name.casefold() for name in names}
        return True, sorted(wanted & running), None

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        d = resolved.desired
        manager, pkg_id = d["manager"], d["id"]
        mgr = MANAGERS.get(manager)
        if mgr is None:
            return ResourceResult(self.TYPE, pkg_id, False, dry_run, "skip",
                                  skipped_reason=f"no handler for package manager '{manager}'")
        if ctx.platform not in mgr["platforms"]:
            return ResourceResult(self.TYPE, pkg_id, False, dry_run, "skip",
                                  skipped_reason=f"manager '{manager}' not supported on "
                                                 f"'{ctx.platform}'")
        if shutil.which(mgr["bin"]) is None:
            return ResourceResult(self.TYPE, pkg_id, False, dry_run, "skip",
                                  skipped_reason=f"manager binary '{mgr['bin']}' not on PATH")

        live = self._detect(ctx, mgr, pkg_id)
        ver = d.get("version")
        want_absent = d.get("state") == "absent"
        commands: list[list[str]] = []
        guard = d.get("process_guard") or {}

        if guard.get("names") and not live.get("known", True):
            return ResourceResult(
                self.TYPE, pkg_id, False, dry_run, "defer",
                detail="deferred package mutation because installed state is unknown",
                deferred_reason=(
                    f"package probe `{' '.join(mgr['detect'](pkg_id, None))}` "
                    "did not establish installed state"
                ),
            )

        if want_absent:
            if not live["present"]:
                return ResourceResult(self.TYPE, pkg_id, False, dry_run, "none",
                                      detail="already absent")
            commands.append(mgr["uninstall"](pkg_id, ver))
            action = "uninstall"
        else:
            needs_install = not live["present"]
            needs_update = bool(live["present"] and ver and live.get("version") != ver)
            if needs_install:
                commands.append(mgr["install"](pkg_id, ver))
            elif needs_update:
                commands.append(mgr["update"](pkg_id, ver))
            if d.get("pin") and mgr.get("pin"):
                pin = self._detect_pin(ctx, mgr, pkg_id)
                if not self._matches_desired(pin, ver):
                    commands.append(mgr["pin"](pkg_id, ver))
            if not commands:
                return ResourceResult(self.TYPE, pkg_id, False, dry_run, "none",
                                      detail=f"present{('@' + ver) if ver else ''}")
            action = "install" if needs_install else ("update" if needs_update else "pin")

        guarded_replacement = action in {"update", "uninstall"}
        if guarded_replacement and guard.get("names"):
            known, running, unknown_reason = self._process_guard(ctx, guard["names"])
            if not known or running:
                if running:
                    reason = f"process guard matched running: {', '.join(running)}"
                else:
                    reason = f"process guard state is unknown: {unknown_reason}"
                return ResourceResult(
                    self.TYPE, pkg_id, False, dry_run, "defer",
                    detail=(
                        f"deferred {action} from {live.get('version') or 'present'}"
                        f"{(' to ' + ver) if ver else ''}"
                    ),
                    deferred_reason=reason,
                    commands=commands,
                )

        if dry_run:
            detail = " ; ".join(" ".join(c) for c in commands)
            return ResourceResult(self.TYPE, pkg_id, True, True, action,
                                  detail=detail, commands=commands)

        verified_nonzero: list[str] = []
        for argv in commands:
            out = ctx.runner(argv)
            if manager == "winget" and len(argv) > 1 and argv[1] in {
                "install", "upgrade", "uninstall", "pin"
            }:
                operation = argv[1]
                label = "pin" if operation == "pin" else "package"
                postcondition = (
                    self._detect_pin(ctx, mgr, pkg_id)
                    if label == "pin"
                    else self._detect(ctx, mgr, pkg_id)
                )
                satisfied = (
                    bool(
                        postcondition.get("known", True)
                        and not postcondition["present"]
                    )
                    if operation == "uninstall"
                    else self._matches_desired(postcondition, ver)
                )
                if satisfied:
                    if out.returncode != 0:
                        verified_nonzero.append(label)
                    continue
                command_detail = (
                    f"exited {out.returncode}: "
                    f"{(out.stderr or out.stdout).strip()[:200]}"
                    if out.returncode != 0
                    else "completed without satisfying the exact postcondition"
                )
                return ResourceResult(
                    self.TYPE, pkg_id, True, False, "error", commands=commands,
                    detail=f"`{' '.join(argv)}` {command_detail}",
                )
            if out.returncode != 0:
                return ResourceResult(
                    self.TYPE, pkg_id, True, False, "error", commands=commands,
                    detail=f"`{' '.join(argv)}` exited {out.returncode}: "
                           f"{(out.stderr or out.stdout).strip()[:200]}")
        detail = "applied"
        if verified_nonzero:
            detail += f" (verified {' and '.join(verified_nonzero)} postcondition)"
        return ResourceResult(self.TYPE, pkg_id, True, False, action,
                              detail=detail, commands=commands)


class FileResourceHandler(ResourceHandler):
    TYPE = "file"

    @staticmethod
    def _marker_begin(decl: dict[str, Any]) -> str:
        override = decl.get("begin")
        return str(override) if override else f"# >>> {decl.get('block')} >>>"

    @staticmethod
    def _marker_end(decl: dict[str, Any]) -> str:
        override = decl.get("end")
        return str(override) if override else f"# <<< {decl.get('block')} <<<"

    def identity(self, decl: dict[str, Any]) -> tuple:
        # A managed-block declaration owns only its marked block, so its identity
        # carries the block id -- distinct blocks in one file are separate
        # (compatible) identities, while whole-file strategies use the empty id.
        block = ""
        if str(decl.get("strategy")) == "managed-block":
            block = str(decl.get("block") or "")
        return ("file", normalize_path_spec(str(decl.get("path"))), block)

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id") or decl.get("path"))

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        decls = [member.declaration for member in members]
        owners = [member.owner for member in members]
        if str(decls[0].get("strategy")) == "managed-block":
            return self._merge_managed_block(members)
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        path = str(decls[0].get("path"))
        who = ", ".join(sorted(set(owners)))
        ident = self.identity(decls[0])

        ordered = sorted(members, key=lambda member: member.sort_key)
        enforce = [
            member for member in ordered
            if member.declaration.get("strategy", "enforce") == "enforce"
        ]
        floor = [
            member for member in ordered
            if member.declaration.get("strategy", "enforce") == "ensure-present"
        ]

        if enforce:
            format_members = enforce
            fmt, selected, _, conflict, decision, info = _select_field(
                format_members,
                ident,
                "format",
                lambda member: str(member.declaration.get("format", "text")),
            )
            if conflict:
                formats = sorted({
                    str(member.declaration.get("format", "text"))
                    for member in selected
                })
                findings.append(ResourceFinding(
                    "error", "resource-conflict",
                    f"file '{path}' is declared with conflicting formats {formats} "
                    f"across packages: "
                    f"{', '.join(sorted(member.owner for member in selected))}.",
                ))
            if decision:
                decisions.append(decision)
                findings.append(info)
            content, selected, _, conflict, decision, info = _select_field(
                enforce,
                ident,
                "content",
                lambda member: member.declaration.get("content", ""),
            )
            if conflict:
                findings.append(ResourceFinding(
                    "error", "resource-conflict",
                    f"file '{path}' is enforced to conflicting content by "
                    f"{', '.join(sorted(member.owner for member in selected))}.",
                ))
            if decision:
                decisions.append(decision)
                findings.append(info)
            if floor:
                findings.append(ResourceFinding(
                    "advisory", "resource-precedence",
                    f"file '{path}' has both enforce and ensure-present declarations; "
                    f"enforce content wins.",
                ))
            strategy = "enforce"
        else:
            format_members = floor
            fmt, selected, _, conflict, decision, info = _select_field(
                format_members,
                ident,
                "format",
                lambda member: str(member.declaration.get("format", "text")),
            )
            if conflict:
                formats = sorted({
                    str(member.declaration.get("format", "text"))
                    for member in selected
                })
                findings.append(ResourceFinding(
                    "error", "resource-conflict",
                    f"file '{path}' is declared with conflicting formats {formats} "
                    f"across packages: "
                    f"{', '.join(sorted(member.owner for member in selected))}.",
                ))
            if decision:
                decisions.append(decision)
                findings.append(info)
            content, selected, _, conflict, decision, info = _select_field(
                floor,
                ident,
                "content",
                lambda member: member.declaration.get("content", ""),
            )
            if conflict:
                findings.append(ResourceFinding(
                    "advisory", "resource-precedence",
                    f"file '{path}' has ensure-present declarations with differing "
                    f"content across {who}; the first by owner order is used.",
                ))
            if decision:
                decisions.append(decision)
                findings.append(info)
            strategy = "ensure-present"

        desired = {"path": path, "format": fmt, "strategy": strategy,
                   "content": content}
        return desired, findings, decisions

    def _merge_managed_block(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        """Resolve a group that all target the *same* ``(path, block)`` identity."""
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        decls = [member.declaration for member in members]
        path = str(decls[0].get("path"))
        block = str(decls[0].get("block"))
        ident = self.identity(decls[0])

        marker_sets = {
            (
                self._marker_begin(member.declaration),
                self._marker_end(member.declaration),
            )
            for member in members
        }
        begin, end = sorted(marker_sets)[0]
        if len(marker_sets) > 1:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"managed block '{block}' in '{path}' has conflicting begin/end markers "
                f"across packages: {', '.join(sorted(member.owner for member in members))}.",
            ))

        state, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "state",
            lambda member: str(member.declaration.get("state", "present")),
        )
        if conflict:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"managed block '{block}' in '{path}' is declared both present and absent "
                f"across packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)

        content, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "content",
            lambda member: member.declaration.get("content", ""),
        )
        if conflict:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"managed block '{block}' in '{path}' has conflicting content across "
                f"packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        desired = {"path": path, "format": "text", "strategy": "managed-block",
                   "block": block, "begin": begin, "end": end, "state": state,
                   "content": content}
        return desired, findings, decisions

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        d = resolved.desired
        target = resolve_file_path(str(d["path"]), ctx.home, ctx.repo_paths)
        if target is None:
            return ResourceResult(self.TYPE, resolved.id, False, dry_run, "skip",
                                  skipped_reason=f"could not resolve path '{d['path']}' "
                                                 f"(unknown repo anchor)")
        strategy = d.get("strategy", "enforce")
        exists = target.exists()

        if strategy == "managed-block":
            return self._apply_managed_block(resolved, target, exists, dry_run)
        fmt = d.get("format", "text")
        if fmt == "json":
            return self._apply_json(resolved, target, exists, strategy, dry_run)
        return self._apply_text(resolved, target, exists, strategy, dry_run)

    def _apply_managed_block(self, resolved, target: Path, exists: bool,
                             dry_run: bool) -> ResourceResult:
        d = resolved.desired
        begin, end = str(d["begin"]), str(d["end"])
        body = str(d.get("content", ""))
        present = d.get("state", "present") != "absent"
        current = target.read_text(encoding="utf-8") if exists else ""

        if not present:
            if not _block_present(current, begin, end):
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                      detail="block already absent")
            desired_text = _render_managed_block(current, begin, end, body, present=False)
            action = "remove-block"
        else:
            desired_text = _render_managed_block(current, begin, end, body, present=True)
            if exists and current == desired_text:
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                      detail="up-to-date")
            action = "write-block"

        if dry_run:
            return ResourceResult(self.TYPE, resolved.id, True, True, action,
                                  detail=f"would update block '{d.get('block')}' in {target}")
        backup = backup_file(target) if exists else None
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(desired_text, encoding="utf-8")
        tmp.replace(target)
        return ResourceResult(self.TYPE, resolved.id, True, False, action,
                              detail=f"wrote block '{d.get('block')}' in {target}",
                              backup_path=str(backup) if backup else None)

    def _apply_text(self, resolved, target: Path, exists: bool, strategy: str,
                    dry_run: bool) -> ResourceResult:
        desired = str(resolved.desired.get("content", ""))
        current = target.read_text(encoding="utf-8") if exists else None
        if strategy == "ensure-present":
            if exists:
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                      detail="present (ensure-present, left as-is)")
            action = "write"
        else:  # enforce
            if current == desired:
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                      detail="up-to-date")
            action = "write"
        if dry_run:
            return ResourceResult(self.TYPE, resolved.id, True, True, action,
                                  detail=f"would write {target}")
        backup = backup_file(target) if exists else None
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(desired, encoding="utf-8")
        tmp.replace(target)
        return ResourceResult(self.TYPE, resolved.id, True, False, action,
                              detail=f"wrote {target}",
                              backup_path=str(backup) if backup else None)

    def _apply_json(self, resolved, target: Path, exists: bool, strategy: str,
                    dry_run: bool) -> ResourceResult:
        from .surfaces._common import merge_enforce, merge_floor
        content = resolved.desired.get("content")
        if isinstance(content, str):
            import json
            try:
                content = json.loads(content) if content.strip() else {}
            except json.JSONDecodeError as exc:
                return ResourceResult(
                    self.TYPE,
                    resolved.id,
                    False,
                    dry_run,
                    "error",
                    detail=f"content is not valid JSON: {exc}",
                )
        live = read_json(target) if exists else {}
        merged = merge_floor(live, content) if strategy == "ensure-present" \
            else merge_enforce(live, content)
        if merged == live and exists:
            return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                  detail="up-to-date")
        if dry_run:
            return ResourceResult(self.TYPE, resolved.id, True, True, "write",
                                  detail=f"would write {target}")
        backup = backup_file(target) if exists else None
        write_json_atomic(target, merged)
        return ResourceResult(self.TYPE, resolved.id, True, False, "write",
                              detail=f"wrote {target}",
                              backup_path=str(backup) if backup else None)


# --------------------------------------------------------------------------- #
# Registry handler (Windows, via reg.exe through the injectable runner)
# --------------------------------------------------------------------------- #
#: Hive short-name aliases -> canonical ``HKEY_*`` names.
_HIVE_ALIASES = {
    "HKCU": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKCR": "HKEY_CLASSES_ROOT",
    "HKU": "HKEY_USERS",
    "HKCC": "HKEY_CURRENT_CONFIG",
}

#: Friendly ``value_type`` -> ``reg.exe`` ``REG_*`` type.
REGISTRY_TYPE_MAP = {
    "String": "REG_SZ",
    "ExpandString": "REG_EXPAND_SZ",
    "MultiString": "REG_MULTI_SZ",
    "DWord": "REG_DWORD",
    "QWord": "REG_QWORD",
    "Binary": "REG_BINARY",
}
_REG_VALUE_RE = re.compile(r"\s(REG_[A-Z_]+)\s+(.*)$")


def canonical_reg_key(path: str) -> str:
    """A stable ``HKEY_*\\Sub\\Key`` form (drops any PSDrive colon, expands hive)."""
    raw = str(path).replace("/", "\\").strip().strip("\\")
    parts = raw.split("\\", 1)
    hive = parts[0].rstrip(":").upper()
    hive = _HIVE_ALIASES.get(hive, hive)
    rest = parts[1] if len(parts) > 1 else ""
    return f"{hive}\\{rest}" if rest else hive


def _parse_reg_query(stdout: str, name: str) -> tuple[str | None, str | None]:
    """Extract ``(data, REG_type)`` for ``name`` (``""`` = the default value)."""
    target = name.lower() if name else "(default)"
    for line in stdout.splitlines():
        m = _REG_VALUE_RE.search(line)
        if not m:
            continue
        vname = line[: m.start()].strip()
        if vname.lower() == target:
            return m.group(2).strip(), m.group(1)
    return None, None


class RegistryResourceHandler(ResourceHandler):
    TYPE = "registry"

    def identity(self, decl: dict[str, Any]) -> tuple:
        # Registry keys and value names are case-insensitive, so identity folds
        # case; the concrete (cased) key is carried in ``desired`` for reg.exe.
        key = canonical_reg_key(str(decl.get("path"))).lower()
        name = str(decl.get("name") or "").lower()
        return ("registry", key, name)

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id") or decl.get("path"))

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        if not super().applies_on(decl, plat):
            return False
        return plat == "windows"

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        decls = [member.declaration for member in members]
        key = canonical_reg_key(str(decls[0].get("path")))
        name = str(decls[0].get("name") or "")
        label = f"{key}\\{name or '(Default)'}"
        ident = self.identity(decls[0])

        state, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "state",
            lambda member: str(member.declaration.get("state", "present")),
        )
        if conflict:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"registry value '{label}' is declared both present and absent across "
                f"packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        value, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "value",
            lambda member: member.declaration.get("value"),
            include=lambda member: member.declaration.get("value") is not None,
        )
        if conflict:
            values = sorted({
                str(member.declaration.get("value")) for member in selected
            })
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"registry value '{label}' is set to conflicting values {values} "
                f"across packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        vtype, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "value_type",
            lambda member: str(member.declaration.get("value_type", "String")),
        )
        if conflict:
            vtypes = sorted({
                str(member.declaration.get("value_type", "String"))
                for member in selected
            })
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"registry value '{label}' has conflicting value types {vtypes} "
                f"across packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        desired = {"key": key, "name": name, "value": value,
                   "value_type": vtype, "state": state}
        return desired, findings, decisions

    def _detect(self, ctx: ResourceContext, key: str, name: str) -> dict[str, Any]:
        argv = ["reg", "query", key] + (["/v", name] if name else ["/ve"])
        try:
            out = ctx.runner(argv)
        except (OSError, subprocess.SubprocessError):
            return {"present": False, "value": None, "reg_type": None}
        if out.returncode != 0:
            return {"present": False, "value": None, "reg_type": None}
        value, reg_type = _parse_reg_query(out.stdout, name)
        return {"present": True, "value": value, "reg_type": reg_type}

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        d = resolved.desired
        if ctx.platform != "windows":
            return ResourceResult(self.TYPE, resolved.id, False, dry_run, "skip",
                                  skipped_reason="registry resources apply on Windows only")
        if shutil.which("reg") is None:
            return ResourceResult(self.TYPE, resolved.id, False, dry_run, "skip",
                                  skipped_reason="'reg' not on PATH")
        key, name = d["key"], d.get("name", "")
        live = self._detect(ctx, key, name)
        commands: list[list[str]] = []

        if d.get("state") == "absent":
            if not live["present"]:
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                      detail="already absent")
            commands.append(["reg", "delete", key]
                            + (["/v", name] if name else ["/ve"]) + ["/f"])
            action = "delete"
        else:
            reg_type = REGISTRY_TYPE_MAP.get(str(d.get("value_type", "String")), "REG_SZ")
            value = "" if d.get("value") is None else str(d.get("value"))
            needs = ((not live["present"])
                     or live.get("value") != value
                     or (live.get("reg_type") not in (None, reg_type)))
            if not needs:
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "none",
                                      detail="up-to-date")
            commands.append(["reg", "add", key] + (["/v", name] if name else ["/ve"])
                            + ["/t", reg_type, "/d", value, "/f"])
            action = "write"

        if dry_run:
            return ResourceResult(self.TYPE, resolved.id, True, True, action,
                                  detail=" ; ".join(" ".join(c) for c in commands),
                                  commands=commands)
        for argv in commands:
            out = ctx.runner(argv)
            if out.returncode != 0:
                return ResourceResult(
                    self.TYPE, resolved.id, True, False, "error", commands=commands,
                    detail=f"`{' '.join(argv)}` exited {out.returncode}: "
                           f"{(out.stderr or out.stdout).strip()[:200]}")
        return ResourceResult(self.TYPE, resolved.id, True, False, action,
                              detail="applied", commands=commands)


# --------------------------------------------------------------------------- #
# Feature handler (Windows optional features / capabilities; Linux/WSL units)
# --------------------------------------------------------------------------- #
def _parse_dism_feature(out: RunOutcome, fid: str) -> dict[str, Any]:
    present = out.returncode == 0 and bool(
        re.search(r"State\s*:\s*Enabled", out.stdout, re.IGNORECASE))
    return {"present": present}


def _parse_dism_capability(out: RunOutcome, fid: str) -> dict[str, Any]:
    present = out.returncode == 0 and bool(
        re.search(r"State\s*:\s*Installed", out.stdout, re.IGNORECASE))
    return {"present": present}


def _parse_systemctl(out: RunOutcome, fid: str) -> dict[str, Any]:
    first = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
    return {"present": out.returncode == 0 and first in ("enabled", "enabled-runtime")}


FEATURE_MANAGERS: dict[str, dict[str, Any]] = {
    "windows-optional-feature": {
        "platforms": {"windows"},
        "bin": "dism",
        "parse_detect": _parse_dism_feature,
        "detect": lambda i: ["dism", "/online", "/get-featureinfo", f"/featurename:{i}"],
        "enable": lambda i: ["dism", "/online", "/enable-feature",
                             f"/featurename:{i}", "/norestart"],
        "disable": lambda i: ["dism", "/online", "/disable-feature",
                              f"/featurename:{i}", "/norestart"],
    },
    "windows-capability": {
        "platforms": {"windows"},
        "bin": "dism",
        "parse_detect": _parse_dism_capability,
        "detect": lambda i: ["dism", "/online", "/get-capabilityinfo",
                             f"/capabilityname:{i}"],
        "enable": lambda i: ["dism", "/online", "/add-capability",
                             f"/capabilityname:{i}"],
        "disable": lambda i: ["dism", "/online", "/remove-capability",
                              f"/capabilityname:{i}"],
    },
    "linux-systemd": {
        "platforms": {"linux", "wsl"},
        "bin": "systemctl",
        "parse_detect": _parse_systemctl,
        "detect": lambda i: ["systemctl", "is-enabled", i],
        "enable": lambda i: ["systemctl", "enable", i],
        "disable": lambda i: ["systemctl", "disable", i],
    },
}


class FeatureResourceHandler(ResourceHandler):
    TYPE = "feature"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return ("feature", str(decl.get("manager")), str(decl.get("id")).lower())

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id"))

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        if not super().applies_on(decl, plat):
            return False
        mgr = FEATURE_MANAGERS.get(str(decl.get("manager")))
        # An unknown manager still 'applies' (skip reported at apply); a known
        # manager filters by the platforms it supports.
        return mgr is None or plat in mgr["platforms"]

    def merge(
        self, members: list[ResourceContribution]
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        decls = [member.declaration for member in members]
        ident = self.identity(decls[0])
        state, selected, _, conflict, decision, info = _select_field(
            members,
            ident,
            "state",
            lambda member: str(member.declaration.get("state", "present")),
        )
        if conflict:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"feature '{ident[2]}' ({ident[1]}) is declared both present and absent "
                f"across packages: "
                f"{', '.join(sorted(member.owner for member in selected))}.",
            ))
        if decision:
            decisions.append(decision)
            findings.append(info)
        desired = {"manager": ident[1], "id": str(decls[0].get("id")), "state": state}
        return desired, findings, decisions

    def _detect(self, ctx: ResourceContext, mgr: dict, fid: str) -> dict[str, Any]:
        try:
            out = ctx.runner(mgr["detect"](fid))
        except (OSError, subprocess.SubprocessError):
            return {"present": False}
        return mgr["parse_detect"](out, fid)

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        d = resolved.desired
        manager, fid = d["manager"], d["id"]
        mgr = FEATURE_MANAGERS.get(manager)
        if mgr is None:
            return ResourceResult(self.TYPE, fid, False, dry_run, "skip",
                                  skipped_reason=f"no handler for feature manager '{manager}'")
        if ctx.platform not in mgr["platforms"]:
            return ResourceResult(self.TYPE, fid, False, dry_run, "skip",
                                  skipped_reason=f"manager '{manager}' not supported on "
                                                 f"'{ctx.platform}'")
        if shutil.which(mgr["bin"]) is None:
            return ResourceResult(self.TYPE, fid, False, dry_run, "skip",
                                  skipped_reason=f"manager binary '{mgr['bin']}' not on PATH")

        live = self._detect(ctx, mgr, fid)
        commands: list[list[str]] = []
        if d.get("state") == "absent":
            if not live["present"]:
                return ResourceResult(self.TYPE, fid, False, dry_run, "none",
                                      detail="already absent")
            commands.append(mgr["disable"](fid))
            action = "disable"
        else:
            if live["present"]:
                return ResourceResult(self.TYPE, fid, False, dry_run, "none",
                                      detail="already present")
            commands.append(mgr["enable"](fid))
            action = "enable"

        if dry_run:
            return ResourceResult(self.TYPE, fid, True, True, action,
                                  detail=" ; ".join(" ".join(c) for c in commands),
                                  commands=commands)
        for argv in commands:
            out = ctx.runner(argv)
            if out.returncode != 0:
                return ResourceResult(
                    self.TYPE, fid, True, False, "error", commands=commands,
                    detail=f"`{' '.join(argv)}` exited {out.returncode}: "
                           f"{(out.stderr or out.stdout).strip()[:200]}")
        return ResourceResult(self.TYPE, fid, True, False, action,
                              detail="applied", commands=commands)


# --------------------------------------------------------------------------- #
# Windows power-setting handler (active power scheme, via powercfg)
# --------------------------------------------------------------------------- #
_TRAILING_HEX_RE = re.compile(r"0x([0-9a-f]+)\s*$", re.IGNORECASE | re.MULTILINE)
_GUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _parse_power_query(out: RunOutcome) -> dict[str, Any]:
    if out.returncode != 0:
        return {"known": False, "ac": None, "dc": None}
    # The current AC/DC indexes are the final two 0x-prefixed values in /QH
    # output. Parsing by position avoids localized English labels.
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
        return str(
            decl.get("id")
            or f"{decl.get('subgroup')}/{decl.get('setting')}"
        )

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
                lambda member: normalize_power_setting_value(
                    member.declaration[source]
                ),
                include=lambda member: source in member.declaration,
            )
            if conflict:
                values = sorted({
                    normalize_power_setting_value(member.declaration[source])
                    for member in selected
                })
                findings.append(ResourceFinding(
                    "error",
                    "resource-conflict",
                    f"power setting '{label}' has conflicting {source.upper()} "
                    f"values {values} across packages: "
                    f"{', '.join(sorted(member.owner for member in selected))}.",
                ))
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

    def _target_is_active(
        self, ctx: ResourceContext, desired: dict[str, Any]
    ) -> bool | None:
        if desired["scheme"] == "scheme_current":
            return True
        try:
            active = _parse_active_scheme(
                ctx.runner(["powercfg", "/GETACTIVESCHEME"])
            )
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
                setter_commands.append((
                    source,
                    [
                        "powercfg",
                        verb,
                        d["scheme"],
                        d["subgroup"],
                        d["setting"],
                        str(d[source]),
                    ],
                ))
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
                rollback_failures = self._rollback(
                    ctx, d, live, applied_sources, target_active
                )
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
            source
            for source in ("ac", "dc")
            if source in d and verified.get(source) != d[source]
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


# --------------------------------------------------------------------------- #
# Package-manager table (argv templates + detect parsers)
# --------------------------------------------------------------------------- #
def _winget_exact_row(out: RunOutcome, pkg_id: str) -> list[str] | None:
    """Return the WinGet row containing ``pkg_id`` as an exact token."""
    if out.returncode != 0:
        return None
    wanted = pkg_id.casefold()
    for line in out.stdout.splitlines():
        tokens = line.split()
        if any(token.casefold() == wanted for token in tokens):
            return tokens
    return None


def _parse_winget(out: RunOutcome, pkg_id: str) -> dict[str, Any]:
    row = _winget_exact_row(out, pkg_id)
    if row is None:
        return {"present": False, "version": None}
    index = next(i for i, token in enumerate(row) if token.casefold() == pkg_id.casefold())
    version = row[index + 1] if index + 1 < len(row) else None
    return {"present": True, "version": version}


def _parse_winget_pin(out: RunOutcome, pkg_id: str) -> dict[str, Any]:
    row = _winget_exact_row(out, pkg_id)
    if row is None:
        return {"present": False, "version": None}
    index = next(i for i, token in enumerate(row) if token.casefold() == pkg_id.casefold())
    tail = row[index + 1:]
    version = tail[-1] if tail else None
    return {"present": True, "version": version}


def _parse_dpkg(out: RunOutcome, pkg_id: str) -> dict[str, Any]:
    present = out.returncode == 0 and bool(out.stdout.strip())
    return {"present": present, "version": out.stdout.strip() or None}


def _parse_line_list(out: RunOutcome, pkg_id: str) -> dict[str, Any]:
    present = out.returncode == 0 and pkg_id.lower() in out.stdout.lower()
    version = None
    if present:
        m = _SEMVER_RE.search(out.stdout)
        version = m.group(0) if m else None
    return {"present": present, "version": version}


def _parse_tasklist(out: RunOutcome) -> set[str]:
    """Parse Windows ``tasklist /FO CSV /NH`` output to folded image names."""
    running: set[str] = set()
    for row in csv.reader(io.StringIO(out.stdout)):
        if row and row[0].strip():
            running.add(row[0].strip().casefold())
    return running


PROCESS_GUARD_PROBES: dict[str, dict[str, Any]] = {
    "windows": {
        "bin": "tasklist",
        "command": lambda: ["tasklist", "/FO", "CSV", "/NH"],
        "parse": _parse_tasklist,
    },
}


_WINGET_NO_APPLICATIONS_FOUND = 0x8A150014
_WINGET_ABSENT_RETURN_CODES = {
    _WINGET_NO_APPLICATIONS_FOUND,
    _WINGET_NO_APPLICATIONS_FOUND - (1 << 32),
}


MANAGERS: dict[str, dict[str, Any]] = {
    "winget": {
        "platforms": {"windows"},
        "bin": "winget",
        "absent_returncodes": _WINGET_ABSENT_RETURN_CODES,
        "parse_detect": _parse_winget,
        "detect": lambda i, v: ["winget", "list", "--id", i, "--exact",
                                "--accept-source-agreements"],
        "parse_pin": _parse_winget_pin,
        "detect_pin": lambda i: ["winget", "pin", "list", "--id", i, "--exact"],
        "install": lambda i, v: ["winget", "install", "--id", i, "--exact",
                                 *(["--version", v] if v else []),
                                 "--accept-source-agreements", "--accept-package-agreements"],
        "update": lambda i, v: ["winget", "upgrade", "--id", i, "--exact",
                                *(["--version", v] if v else []), "--include-pinned",
                                "--accept-source-agreements",
                                "--accept-package-agreements"],
        "pin": lambda i, v: ["winget", "pin", "add", "--id", i, "--exact",
                             *(["--version", v] if v else []), "--force"],
        "uninstall": lambda i, v: ["winget", "uninstall", "--id", i, "--exact"],
    },
    "apt": {
        "platforms": {"linux", "wsl"},
        "bin": "apt-get",
        "parse_detect": _parse_dpkg,
        "detect": lambda i, v: ["dpkg-query", "-W", "-f=${Version}", i],
        "install": lambda i, v: ["apt-get", "install", "-y",
                                 f"{i}={v}" if v else i],
        "update": lambda i, v: ["apt-get", "install", "-y",
                                f"{i}={v}" if v else i],
        "pin": lambda i, v: ["apt-mark", "hold", i],
        "uninstall": lambda i, v: ["apt-get", "remove", "-y", i],
    },
    "pipx": {
        "platforms": {"windows", "linux", "wsl"},
        "bin": "pipx",
        "parse_detect": _parse_line_list,
        "detect": lambda i, v: ["pipx", "list", "--short"],
        "install": lambda i, v: ["pipx", "install", f"{i}=={v}" if v else i],
        "update": lambda i, v: ["pipx", "install", "--force",
                                f"{i}=={v}" if v else i],
        "pin": None,
        "uninstall": lambda i, v: ["pipx", "uninstall", i],
    },
    "uv-tool": {
        "platforms": {"windows", "linux", "wsl"},
        "bin": "uv",
        "parse_detect": _parse_line_list,
        "detect": lambda i, v: ["uv", "tool", "list"],
        "install": lambda i, v: ["uv", "tool", "install", f"{i}=={v}" if v else i],
        "update": lambda i, v: ["uv", "tool", "install", "--force",
                                f"{i}=={v}" if v else i],
        "pin": None,
        "uninstall": lambda i, v: ["uv", "tool", "uninstall", i],
    },
    "pip": {
        "platforms": {"windows", "linux", "wsl"},
        "bin": "pip",
        "parse_detect": _parse_line_list,
        "detect": lambda i, v: ["pip", "show", i],
        "install": lambda i, v: ["pip", "install", f"{i}=={v}" if v else i],
        "update": lambda i, v: ["pip", "install", "--upgrade",
                                f"{i}=={v}" if v else i],
        "pin": None,
        "uninstall": lambda i, v: ["pip", "uninstall", "-y", i],
    },
}


HANDLERS: dict[str, ResourceHandler] = {
    "package": PackageResourceHandler(),
    "file": FileResourceHandler(),
    "registry": RegistryResourceHandler(),
    "feature": FeatureResourceHandler(),
    "power-setting": PowerSettingResourceHandler(),
}


# --------------------------------------------------------------------------- #
# Resolution across the package union
# --------------------------------------------------------------------------- #
def _resource_gate_ok(decl: dict[str, Any], pkg: RequirementPackage, machine: str) -> bool:
    gate = decl.get("gate")
    if gate:
        return machine in gate or "*" in gate
    return True  # package gate already applied by resolve_union


def group_resources(
    packages: list[RequirementPackage], machine: str, plat: str
) -> dict[tuple, list[tuple[RequirementPackage, dict[str, Any]]]]:
    """Group applicable resource declarations by cross-package identity."""
    groups: dict[tuple, list[tuple[RequirementPackage, dict[str, Any]]]] = {}
    for pkg in packages:
        for decl in pkg.resources:
            rtype = str(decl.get("type"))
            handler = HANDLERS.get(rtype)
            if not _resource_gate_ok(decl, pkg, machine):
                continue
            if handler is not None and not handler.applies_on(decl, plat):
                continue
            if handler is not None:
                ident = handler.identity(decl)
            else:  # reserved type (registry/feature): identity by type+id/path
                ident = (rtype, str(decl.get("id") or decl.get("path")))
            groups.setdefault(ident, []).append((pkg, decl))
    return groups


def resolve_resources(
    packages: list[RequirementPackage], machine: str, plat: str
) -> tuple[list[ResolvedResource], list[ResourceFinding]]:
    """Resolve every resource identity to a single desired state + findings."""
    resolved: list[ResolvedResource] = []
    findings: list[ResourceFinding] = []
    for ident, members in sorted(group_resources(packages, machine, plat).items(),
                                 key=lambda kv: [str(x) for x in kv[0]]):
        rtype = ident[0]
        decls = [d for _, d in members]
        contributions = sorted(
            [
                ResourceContribution(
                    package=pkg,
                    declaration=decl,
                    owner=str(decl.get("owner") or pkg.name),
                )
                for pkg, decl in members
            ],
            key=lambda member: member.sort_key,
        )
        owners = [member.owner for member in contributions]
        contributor_details = [member.provenance for member in contributions]
        handler = HANDLERS.get(rtype)
        if handler is None:
            # Reserved type -- carry it through so plan lists it, but there is
            # nothing to merge or apply yet.
            resolved.append(ResolvedResource(
                rtype, str(decls[0].get("id") or decls[0].get("path")),
                ident, dict(decls[0]), sorted(set(owners)),
                contributor_details=contributor_details,
            ))
            continue
        desired, fnd, decisions = handler.merge(contributions)
        findings.extend(fnd)
        resolved.append(ResolvedResource(
            rtype,
            handler.display_id(contributions[0].declaration),
            ident,
            desired,
            sorted(set(owners)),
            contributor_details=contributor_details,
            authority_decisions=sort_decisions(decisions),
        ))
    findings.extend(_file_ownership_findings(resolved))
    return resolved, findings


def _file_ownership_findings(resolved: list[ResolvedResource]) -> list[ResourceFinding]:
    """Flag a path claimed by *both* a whole-file owner and a managed block.

    Distinct managed blocks in one file are compatible (that is the point of the
    strategy); a whole-file ``enforce``/``ensure-present`` owner and any block on
    the same path are not -- the file is either wholly owned or block-owned.
    """
    findings: list[ResourceFinding] = []
    by_path: dict[str, list[ResolvedResource]] = {}
    for res in resolved:
        if res.type == "file":
            by_path.setdefault(res.identity[1], []).append(res)
    for path, group in sorted(by_path.items()):
        blocks = {res.identity[2] for res in group}
        if "" in blocks and any(b != "" for b in blocks):
            owners = sorted({o for res in group for o in res.contributors})
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"file '{path}' has both whole-file and managed-block declarations "
                f"across packages: {', '.join(owners)}; a file is either wholly owned "
                f"or block-owned, not both.",
            ))
    return findings


def detect_conflicts(
    packages: list[RequirementPackage], machine: str, plat: str
) -> list[ResourceFinding]:
    """Manifest-only collision findings for the validator."""
    return resolve_resources(packages, machine, plat)[1]


def apply_resources(
    packages: list[RequirementPackage],
    machine: str,
    plat: str,
    ctx: ResourceContext,
    dry_run: bool = True,
    only: list[str] | None = None,
) -> list[ResourceResult]:
    """Apply every resolved resource (optionally filtered by ``only``)."""
    resolved, _ = resolve_resources(packages, machine, plat)
    results: list[ResourceResult] = []
    for res in resolved:
        if only and not _resource_wanted(res, only):
            continue
        handler = HANDLERS.get(res.type)
        if handler is None:
            results.append(ResourceResult(
                res.type, res.id, False, dry_run, "skip",
                skipped_reason=f"no handler for resource type '{res.type}'"))
            continue
        results.append(handler.apply(res, ctx, dry_run))
    return results


def _resource_wanted(res: ResolvedResource, only: list[str]) -> bool:
    return (res.id in only or res.type in only or f"{res.type}:{res.id}" in only)


def resource_only_names(
    packages: list[RequirementPackage], machine: str, plat: str
) -> set[str]:
    """Every ``only`` token that selects a resource (ids, types, type:id)."""
    names: set[str] = {"resources"}
    for res in resolve_resources(packages, machine, plat)[0]:
        names.update({res.id, res.type, f"{res.type}:{res.id}"})
    return names
