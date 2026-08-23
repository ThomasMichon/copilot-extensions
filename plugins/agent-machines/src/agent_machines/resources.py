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

Two kinds are fully handled today -- ``package`` and ``file``. ``registry`` and
``feature`` are *reserved* identities (recognized by the schema) with no handler
yet: they plan and validate, but apply reports "no handler" so a package can be
authored against them ahead of the engine catching up. Adding a type is a new
``ResourceHandler`` subclass registered in :data:`HANDLERS` -- nothing else in
the engine changes.

**Collision handling mirrors the validator's stance: detect-and-report, resolve
only the unambiguously-compatible.** When two packages target the same resource
identity (same ``(manager, id)`` for a package, same path for a file), compatible
declarations are merged deterministically; incompatible desired values, version
pins, ownership, or strategies raise an ``error`` (or ``advisory``) finding.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from .manifest import RequirementPackage
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

    def summary(self) -> str:
        """A one-word disposition-ish label for plan output."""
        if self.type == "package":
            state = self.desired.get("state", "present")
            ver = self.desired.get("version")
            pinned = " pinned" if self.desired.get("pin") else ""
            return f"{state}{('@' + ver) if ver else ''}{pinned}"
        if self.type == "file":
            return str(self.desired.get("strategy", "enforce"))
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
    backup_path: str | None = None
    commands: list[list[str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.skipped_reason is not None or self.action != "error"


@dataclass
class ResourceContext:
    """Ambient state a handler needs to detect/apply on this machine."""

    home: Path
    repo_paths: dict[str, Path]
    platform: str
    runner: Runner = default_runner


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
        self, decls: list[dict[str, Any]], owners: list[str]
    ) -> tuple[dict[str, Any], list[ResourceFinding]]:  # pragma: no cover - abstract
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
        self, decls: list[dict[str, Any]], owners: list[str]
    ) -> tuple[dict[str, Any], list[ResourceFinding]]:
        findings: list[ResourceFinding] = []
        ident = self.identity(decls[0])
        who = ", ".join(sorted(set(owners)))
        states = {str(d.get("state", "present")) for d in decls}
        state = "present"
        if states == {"absent"}:
            state = "absent"
        elif len(states) > 1:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"package '{ident[2]}' ({ident[1]}) is declared both present and "
                f"absent across packages: {who}.",
            ))
        versions = {str(d["version"]) for d in decls if d.get("version")}
        version = sorted(versions)[0] if versions else None
        if len(versions) > 1:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"package '{ident[2]}' ({ident[1]}) is pinned to conflicting "
                f"versions {sorted(versions)} across packages: {who}.",
            ))
        pin = any(bool(d.get("pin")) for d in decls)
        desired = {"manager": ident[1], "id": ident[2], "state": state,
                   "version": version, "pin": pin}
        return desired, findings

    # -- detect / apply -----------------------------------------------------
    def _detect(self, ctx: ResourceContext, mgr: dict, pkg_id: str) -> dict[str, Any]:
        argv = mgr["detect"](pkg_id, None)
        try:
            out = ctx.runner(argv)
        except (OSError, subprocess.SubprocessError):
            return {"present": False, "version": None}
        return mgr["parse_detect"](out, pkg_id)

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

        if want_absent:
            if not live["present"]:
                return ResourceResult(self.TYPE, pkg_id, False, dry_run, "none",
                                      detail="already absent")
            commands.append(mgr["uninstall"](pkg_id, ver))
            action = "uninstall"
        else:
            needs_install = (not live["present"]) or (ver and live.get("version") != ver)
            if needs_install:
                commands.append(mgr["install"](pkg_id, ver))
            if d.get("pin") and mgr.get("pin"):
                commands.append(mgr["pin"](pkg_id, ver))
            if not commands:
                return ResourceResult(self.TYPE, pkg_id, False, dry_run, "none",
                                      detail=f"present{('@' + ver) if ver else ''}")
            action = "install" if needs_install else "pin"

        if dry_run:
            detail = " ; ".join(" ".join(c) for c in commands)
            return ResourceResult(self.TYPE, pkg_id, True, True, action,
                                  detail=detail, commands=commands)

        for argv in commands:
            out = ctx.runner(argv)
            if out.returncode != 0:
                return ResourceResult(
                    self.TYPE, pkg_id, True, False, "error", commands=commands,
                    detail=f"`{' '.join(argv)}` exited {out.returncode}: "
                           f"{(out.stderr or out.stdout).strip()[:200]}")
        return ResourceResult(self.TYPE, pkg_id, True, False, action,
                              detail="applied", commands=commands)


class FileResourceHandler(ResourceHandler):
    TYPE = "file"

    def identity(self, decl: dict[str, Any]) -> tuple:
        return ("file", normalize_path_spec(str(decl.get("path"))))

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl.get("id") or decl.get("path"))

    def merge(
        self, decls: list[dict[str, Any]], owners: list[str]
    ) -> tuple[dict[str, Any], list[ResourceFinding]]:
        findings: list[ResourceFinding] = []
        path = str(decls[0].get("path"))
        who = ", ".join(sorted(set(owners)))

        formats = {str(d.get("format", "text")) for d in decls}
        fmt = sorted(formats)[0]
        if len(formats) > 1:
            findings.append(ResourceFinding(
                "error", "resource-conflict",
                f"file '{path}' is declared with conflicting formats {sorted(formats)} "
                f"across packages: {who}.",
            ))

        # Order declarations deterministically (owner, then content) so the pick
        # and any advisory are reproducible.
        ordered = sorted(
            zip(decls, owners, strict=False),
            key=lambda t: (t[1], str(t[0].get("content", ""))),
        )
        enforce = [(d, o) for d, o in ordered if d.get("strategy", "enforce") == "enforce"]
        floor = [(d, o) for d, o in ordered if d.get("strategy", "enforce") == "ensure-present"]

        def content_of(pair):
            return pair[0].get("content", "")

        if enforce:
            contents = {str(content_of(p)) for p in enforce}
            if len(contents) > 1:
                findings.append(ResourceFinding(
                    "error", "resource-conflict",
                    f"file '{path}' is enforced to conflicting content by "
                    f"{', '.join(sorted(o for _, o in enforce))}.",
                ))
            if floor:
                findings.append(ResourceFinding(
                    "advisory", "resource-precedence",
                    f"file '{path}' has both enforce and ensure-present declarations; "
                    f"enforce content wins.",
                ))
            chosen = enforce[0]
            strategy = "enforce"
        else:
            contents = {str(content_of(p)) for p in floor}
            if len(contents) > 1:
                findings.append(ResourceFinding(
                    "advisory", "resource-precedence",
                    f"file '{path}' has ensure-present declarations with differing "
                    f"content across {who}; the first by owner order is used.",
                ))
            chosen = floor[0]
            strategy = "ensure-present"

        desired = {"path": path, "format": fmt, "strategy": strategy,
                   "content": chosen[0].get("content", "")}
        return desired, findings

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        d = resolved.desired
        target = resolve_file_path(str(d["path"]), ctx.home, ctx.repo_paths)
        if target is None:
            return ResourceResult(self.TYPE, resolved.id, False, dry_run, "skip",
                                  skipped_reason=f"could not resolve path '{d['path']}' "
                                                 f"(unknown repo anchor)")
        fmt = d.get("format", "text")
        strategy = d.get("strategy", "enforce")
        exists = target.exists()

        if fmt == "json":
            return self._apply_json(resolved, target, exists, strategy, dry_run)
        return self._apply_text(resolved, target, exists, strategy, dry_run)

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
                return ResourceResult(self.TYPE, resolved.id, False, dry_run, "skip",
                                      skipped_reason=f"content is not valid JSON: {exc}")
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
# Package-manager table (argv templates + detect parsers)
# --------------------------------------------------------------------------- #
def _parse_winget(out: RunOutcome, pkg_id: str) -> dict[str, Any]:
    present = out.returncode == 0 and pkg_id.lower() in out.stdout.lower()
    version = None
    if present:
        for line in out.stdout.splitlines():
            if pkg_id.lower() in line.lower():
                m = _SEMVER_RE.search(line)
                if m:
                    version = m.group(0)
                break
    return {"present": present, "version": version}


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


MANAGERS: dict[str, dict[str, Any]] = {
    "winget": {
        "platforms": {"windows"},
        "bin": "winget",
        "parse_detect": _parse_winget,
        "detect": lambda i, v: ["winget", "list", "--id", i, "--exact",
                                "--accept-source-agreements"],
        "install": lambda i, v: ["winget", "install", "--id", i, "--exact",
                                 *(["--version", v] if v else []),
                                 "--accept-source-agreements", "--accept-package-agreements"],
        "pin": lambda i, v: ["winget", "pin", "add", "--id", i,
                             *(["--version", v] if v else [])],
        "uninstall": lambda i, v: ["winget", "uninstall", "--id", i, "--exact"],
    },
    "apt": {
        "platforms": {"linux", "wsl"},
        "bin": "apt-get",
        "parse_detect": _parse_dpkg,
        "detect": lambda i, v: ["dpkg-query", "-W", "-f=${Version}", i],
        "install": lambda i, v: ["apt-get", "install", "-y",
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
        "pin": None,
        "uninstall": lambda i, v: ["pipx", "uninstall", i],
    },
    "uv-tool": {
        "platforms": {"windows", "linux", "wsl"},
        "bin": "uv",
        "parse_detect": _parse_line_list,
        "detect": lambda i, v: ["uv", "tool", "list"],
        "install": lambda i, v: ["uv", "tool", "install", f"{i}=={v}" if v else i],
        "pin": None,
        "uninstall": lambda i, v: ["uv", "tool", "uninstall", i],
    },
    "pip": {
        "platforms": {"windows", "linux", "wsl"},
        "bin": "pip",
        "parse_detect": _parse_line_list,
        "detect": lambda i, v: ["pip", "show", i],
        "install": lambda i, v: ["pip", "install", f"{i}=={v}" if v else i],
        "pin": None,
        "uninstall": lambda i, v: ["pip", "uninstall", "-y", i],
    },
}


HANDLERS: dict[str, ResourceHandler] = {
    "package": PackageResourceHandler(),
    "file": FileResourceHandler(),
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
        owners = [str(d.get("owner") or pkg.name) for pkg, d in members]
        handler = HANDLERS.get(rtype)
        if handler is None:
            # Reserved type -- carry it through so plan lists it, but there is
            # nothing to merge or apply yet.
            resolved.append(ResolvedResource(
                rtype, str(decls[0].get("id") or decls[0].get("path")),
                ident, dict(decls[0]), sorted(set(owners))))
            continue
        desired, fnd = handler.merge(decls, owners)
        findings.extend(fnd)
        resolved.append(ResolvedResource(
            rtype, handler.display_id(decls[0]), ident, desired, sorted(set(owners))))
    return resolved, findings


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
