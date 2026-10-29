#!/usr/bin/env python3
"""Retire observed stale MCP trees through pinned native process identities.

The installer owns admission and lifecycle custody. This finite companion
reuses its process inspector and versioned-runtime attribution. It never trusts
PID files, signals a process group, or treats a stop request as confirmed exit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import installer_engine_footprint as footprint
import installer_engine_uninstall as inspection
from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil.process_retirement import RetirementRefused
from agent_procutil.retirement_custody import RetirementCustody
from versioned_runtime import (
    CURRENT_LINK,
    VERSIONS_DIR,
    _norm_version,
    _publication_lease,
    _slot_of_path,
    current_version,
    list_versions,
)


def _inspect(root: Path) -> dict[int, dict]:
    payload = Path(__file__).absolute().parent.parent
    shell = (
        str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        if os.name == "nt" else ""
    )
    rows = inspection.inspect_processes(
        payload, root, shell, inspection.profile_for(payload, "agent-mcp"), descendants=True,
    )
    return {row["pid"]: row for row in rows}


def _legacy_entries(root: Path) -> tuple[str, ...]:
    declaration = Path(__file__).absolute().with_name("uninstall-profile.json")
    manifest = footprint.parse(footprint.decode(footprint._marker_text(declaration)), "agent-mcp")
    effect = manifest.effects.get("legacy-runtime-processes")
    if not isinstance(effect, dict) or (
        effect["kind"], effect["scope"], effect["disposition"]
    ) != ("process", "runtime-root", "retire"):
        raise RetirementRefused("The maintained legacy-process footprint is unsupported")
    entries = tuple(inspection._leaf(entry) for entry in effect["entries"])
    for entry in entries:
        inspection._plain(root / entry)
    return entries


def _version(
    row: dict, root: Path, versions: set[str], *, legacy_entries: tuple[str, ...] = (),
    current: str | None = None,
) -> str | None:
    arguments = inspection._arguments(row)
    paths = (arguments[0] if arguments else None, row["image"])
    if current is not None:
        selected = {current, *(value for value in versions if _norm_version(value) == _norm_version(current))}
        if any(_slot_of_path(path, str(root / VERSIONS_DIR), selected) for path in paths):
            return current
    for candidate in paths:
        found = _slot_of_path(candidate, str(root / VERSIONS_DIR), versions)
        if found:
            return found
        if candidate:
            path = Path(os.path.abspath(candidate))
            for name in legacy_entries:
                if path.is_relative_to(root / name):
                    return "legacy"
    return None


def _protected(row: dict) -> bool:
    """Keep session hosts and recognizable critical transport out of MCP custody."""
    arguments = inspection._arguments(row)
    names = {Path(row["image"].removesuffix(" (deleted)")).stem.casefold()}
    if arguments:
        names.add(Path(arguments[0]).stem.casefold())
    return bool(names & {
        "copilot", "psmux", "tmux", "ssh", "sshd", "ssh-agent", "devtunnel", "cloudflared",
    }) or any(
        "/@github/copilot/" in argument.replace("\\", "/").casefold()
        for argument in arguments
    )


def _require_retirable(row: dict, current_owner: Callable[[dict], bool] | None = None) -> None:
    if row.get("inspectionIncomplete") or not row.get("image"):
        raise RetirementRefused(f"A selected process cannot be fully inspected (pid={row['pid']})")
    if _protected(row) or (current_owner is not None and current_owner(row)):
        raise RetirementRefused("A stale MCP tree contains a protected session or transport")


def _ancestors(rows: dict[int, dict], pid: int) -> set[int]:
    result = set()
    while pid and pid not in result:
        result.add(pid)
        pid = rows.get(pid, {}).get("ppid", 0)
    return result


def _birth(row: dict) -> str:
    birth = birth_identity(row["pid"])
    if row.get("created") != int(birth.rsplit(":", 1)[1]):
        raise RetirementRefused(
            f"Process identity changed during inspection (pid={row['pid']}, "
            f"observed={row.get('created')}, native={birth.rsplit(':', 1)[1]})"
        )
    return birth


def _descendants(rows: dict[int, dict], selected: set[int]) -> set[int]:
    result = set(selected)
    for _ in range(64):
        for pid in result & rows.keys():
            for child in set(rows[pid].get("children", ())) - rows.keys():
                try:
                    birth_identity(child)
                except ProcessLookupError:
                    continue
                raise RetirementRefused(f"A process child cannot be inspected (pid={child})")
        added = {pid for pid, row in rows.items() if row["ppid"] in result} - result
        if not added:
            return result
        for pid in added:
            parent = rows.get(rows[pid]["ppid"])
            if parent is None or (
                "created" not in parent or "created" not in rows[pid]
                or parent["created"] > rows[pid]["created"]
            ):
                raise RetirementRefused(f"Process parent identity is unverified (pid={pid})")
        result.update(added)
    raise RetirementRefused("Process tree exceeds its inspection budget")


def _retire(
    root: Path, rows: dict[int, dict], targets: set[int], protected: set[int], *,
    check_only: bool, current_owner: Callable[[dict], bool] | None = None,
) -> set[int]:
    scope = _descendants(rows, targets)
    if scope & protected:
        raise RetirementRefused("A stale MCP tree contains a protected session or transport")
    for pid in scope:
        _require_retirable(rows[pid], current_owner)
    if check_only and targets:
        raise RetirementRefused("Old MCP owners remain while version reaping is explicitly disabled")
    retired = set()
    with RetirementCustody(root) as guardian, ExitStack() as custody:
        pinned = {}
        for _ in range(64):
            # Pause parents before discovering further descendants. Once every
            # admitted member is paused, no member can race a new child past census.
            remaining = set(scope) - pinned.keys()
            while remaining:
                ready = {pid for pid in remaining if rows[pid]["ppid"] not in remaining}
                if not ready:
                    raise RetirementRefused("Process ancestry contains a cycle")
                for pid in sorted(ready):
                    row = rows[pid]
                    try:
                        pin = custody.enter_context(guardian.pin(pid, _birth(row)))
                        pin.pause()
                        if pin.exited():
                            raise RetirementRefused(
                                f"A process exited before descendant scope was frozen (pid={pid})"
                            )
                    except ProcessLookupError:
                        raise RetirementRefused(
                            f"A process disappeared before descendant scope was frozen (pid={pid})"
                        ) from None
                    else:
                        pinned[pid] = pin
                remaining -= ready
            later = _inspect(root)
            for pid, pin in pinned.items():
                if pin.exited():
                    raise RetirementRefused(
                        f"A process exited before descendant scope was confirmed (pid={pid})"
                    )
                row = later.get(pid)
                if row is None or _birth(row) != pin.birth:
                    raise RetirementRefused(f"Pinned process inspection is unavailable (pid={pid})")
                _require_retirable(row, current_owner)
            added = _descendants(later, set(pinned)) - scope
            if not added:
                break
            if added & protected:
                raise RetirementRefused("A stale MCP tree gained a protected session or transport")
            for pid in added:
                _require_retirable(later[pid], current_owner)
            scope.update(added)
            rows.update({pid: later[pid] for pid in added})
        else:
            raise RetirementRefused("Process tree did not settle within its inspection budget")
        for pid, pin in pinned.items():
            pin.terminate()
            retired.add(pid)
    return retired


def reap_stale(
    root: Path, *, link_name: str = CURRENT_LINK, exclude_pids: set[int] | None = None,
    fresh: bool = False, check_only: bool = False,
) -> list[dict]:
    """Retire non-current slots and, on fresh recovery, known legacy code owners."""
    root = root.absolute()
    if ".." in root.parts or root == Path(root.anchor) or root in (Path.home(), *Path.home().parents):
        raise RetirementRefused("Runtime retirement refuses filesystem and home ancestors")
    inspection._plain(root)
    current = current_version(root, link_name)
    if current is None:
        raise RetirementRefused("Reaping requires the installer-selected current runtime")
    rows = _inspect(root)
    versions = set(list_versions(root))
    inspection._plain(root / VERSIONS_DIR)
    for version in versions:
        inspection._plain(root / VERSIONS_DIR / version)
    legacy_entries = _legacy_entries(root) if fresh else ()
    classified = {
        pid: version for pid, row in rows.items()
        if (version := _version(row, root, versions, legacy_entries=legacy_entries, current=current))
    }
    stale = {
        pid: version for pid, version in classified.items()
        if _norm_version(version) != _norm_version(current)
    }
    if not stale:
        return []
    if check_only:
        raise RetirementRefused("Old MCP owners remain while version reaping is explicitly disabled")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        raise RetirementRefused("External pinned retirement requires Windows or Linux")
    ancestors = _ancestors(rows, os.getpid())
    excluded = set(exclude_pids or ())
    if excluded - rows.keys():
        raise RetirementRefused("An excluded process root is absent from the native census")
    current_pids = {
        pid for pid, version in classified.items() if _norm_version(version) == _norm_version(current)
    }
    # Explicit exclusions protect their children too, not only one numeric PID.
    protected = _descendants(rows, (excluded | current_pids) & rows.keys()) | excluded | ancestors
    targets = {}
    for pid, version in stale.items():
        if pid in protected:
            if fresh:
                raise RetirementRefused(f"An old MCP owner is protected from retirement (pid={pid})")
            continue
        targets[pid] = version
    if not targets:
        return []

    def current_owner(row: dict) -> bool:
        if current_version(root, link_name) != current:
            raise RetirementRefused("Current runtime selection changed during retirement")
        version = _version(row, root, versions, legacy_entries=legacy_entries, current=current)
        return version is not None and _norm_version(version) == _norm_version(current)

    with _publication_lease(root):
        if current_version(root, link_name) != current:
            raise RetirementRefused("Current runtime selection changed before retirement")
        _retire(root, rows, set(targets), protected, check_only=check_only, current_owner=current_owner)
    remaining = _inspect(root)
    if any(
        (version := _version(row, root, versions, legacy_entries=legacy_entries, current=current))
        and _norm_version(version) != _norm_version(current) and pid not in protected
        for pid, row in remaining.items()
    ):
        raise RetirementRefused("Old MCP runtime owners appeared during retirement")
    return [
        {"version": version, "pid": pid, "terminated": True}
        for pid, version in sorted(targets.items())
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--link-name", default=CURRENT_LINK)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--exclude-pid", action="append", type=int, default=[])
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--reset-control", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        def settle() -> list[dict]:
            return reap_stale(
                args.root, link_name=args.link_name, exclude_pids=set(args.exclude_pid),
                fresh=args.fresh, check_only=args.check_only,
            )

        if args.reset_control:
            if not args.fresh:
                raise RetirementRefused("Control reset requires the fresh construction path")
            from agent_mcp.forward import _serve_handle
            from reset_runtime_control import reset_control

            reaped = reset_control(
                Path(_serve_handle(None)).parent, settle=settle, inspect=lambda: _inspect(args.root),
            )
        else:
            reaped = settle()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"MCP runtime retirement failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"reaped": reaped}))
    else:
        for record in reaped:
            print(f"{record['version']}:{record['pid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
