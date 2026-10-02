#!/usr/bin/env python3
"""Fan the canonical bootstrap-killswitch guard out to every adopting plugin.

The guard (``bootstrap-killswitch-guard.sh`` / ``.ps1``) must physically ship
inside each plugin that has a ``scripts/bootstrap-check.*`` sessionStart hook,
because plugins are pulled **independently** from the marketplace -- it cannot
be a shared runtime import. To keep one source of truth, the canonical copy
lives at ``libs/bootstrap-killswitch/`` and this script vendors it,
**byte-identically**, into every adopting plugin's ``scripts/`` dir.

Opt-in criterion (mirrors ``sync-versioned-runtime.py``'s resolver fan-out):
a plugin adopts by already carrying a ``scripts/bootstrap-check.sh`` (its
sessionStart hook). The guard is vendored alongside it so that hook's own
FIRST lines can call it before running any reconcile logic -- see
``libs/bootstrap-killswitch/README.md`` for the wiring convention each
``bootstrap-check.*`` follows.

``--check`` verifies two separate things, both required for the switch to
actually work for a given plugin: (1) the vendored guard files exist and are
byte-identical to canonical, and (2) each adopter's ``bootstrap-check.sh`` /
``.ps1`` actually CALLS that guard. (2) exists because removing a call-site
block (accidentally, or in a future edit that doesn't touch the vendored
guard file at all) would otherwise leave this check green while silently
disabling the switch for that plugin -- the file being present and correct
says nothing about whether anything actually invokes it.
``agent-index`` ships a deliberate no-op sessionStart stub that never
reconciles anything and is intentionally excluded from the call-site check
(though it still receives the vendored, unused guard files, for a simple
opt-in criterion -- see README.md).

Usage::

    python tools/sync-bootstrap-killswitch.py          # copy canonical -> plugins
    python tools/sync-bootstrap-killswitch.py --check   # verify in sync (CI/pre-push)

``--check`` writes nothing and exits non-zero if any vendored copy is missing,
drifted from canonical, or not actually called from its adopter's
``bootstrap-check.*``.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
CANONICAL_DIR = REPO / "libs" / "bootstrap-killswitch"
GUARD_NAMES = ("bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1")

# A plugin whose bootstrap-check.* deliberately never reconciles anything
# (a no-op sessionStart stub) has nothing for the killswitch to guard --
# excluded from the call-site check, though it still receives the vendored
# (unused) guard files for a simple, uniform opt-in criterion.
NO_RECONCILE_STUBS = frozenset({"agent-index"})

# The exact, distinctive invocation each wired bootstrap-check.* must contain
# -- not just the marker comment (which could survive a careless edit that
# deleted the actual call), but the real guard-check line itself.
CALL_SITE_MARKERS = {
    "bootstrap-check.sh": 'bash "$_bks_guard" check',
    "bootstrap-check.ps1": "& $_bksGuard check",
}


def _adopter_plugins() -> list[Path]:
    """Plugins that ship a sessionStart bootstrap-check hook."""
    return sorted(
        p for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and (p / "scripts" / "bootstrap-check.sh").exists()
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _call_site_gaps(plugins: list[Path]) -> list[str]:
    """Adopters (excluding deliberate no-reconcile stubs) whose
    bootstrap-check.sh/.ps1 does not actually call its vendored guard."""
    gaps: list[str] = []
    for plugin in plugins:
        if plugin.name in NO_RECONCILE_STUBS:
            continue
        for hook_name, marker in CALL_SITE_MARKERS.items():
            hook = plugin / "scripts" / hook_name
            if not hook.exists():
                gaps.append(f"{hook.relative_to(REPO).as_posix()} (missing entirely)")
                continue
            if marker not in hook.read_text(encoding="utf-8", errors="replace"):
                gaps.append(f"{hook.relative_to(REPO).as_posix()} (does not call its vendored guard)")
    return gaps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="verify every plugin copy matches the canonical; write nothing",
    )
    args = ap.parse_args(argv)

    canonical_bytes: dict[str, bytes] = {}
    for name in GUARD_NAMES:
        src = CANONICAL_DIR / name
        if not src.exists():
            print(f"canonical guard missing: {src.relative_to(REPO)}", file=sys.stderr)
            return 1
        canonical_bytes[name] = src.read_bytes()

    plugins = _adopter_plugins()
    if not plugins:
        print("No bootstrap-check adopters found.", file=sys.stderr)
        return 1

    pairs: list[tuple[bytes, Path]] = [
        (canonical_bytes[name], plugin / "scripts" / name)
        for plugin in plugins
        for name in GUARD_NAMES
    ]

    drifted: list[str] = []
    written: list[str] = []
    for want_bytes, dest in pairs:
        current = dest.read_bytes() if dest.exists() else None
        if current is not None and _sha(current) == _sha(want_bytes):
            continue
        rel = dest.relative_to(REPO).as_posix()
        if args.check:
            drifted.append(f"{rel} ({'missing' if current is None else 'drifted'})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(want_bytes)
        if dest.name.endswith(".sh"):
            dest.chmod(dest.stat().st_mode | 0o111)
        written.append(rel)

    scope = f"{len(plugins)} plugins"
    call_site_gaps = _call_site_gaps(plugins)
    if args.check:
        ok = True
        if drifted:
            ok = False
            print(
                "vendored bootstrap-killswitch guard files are out of sync with "
                f"the canonical sources in {CANONICAL_DIR.relative_to(REPO).as_posix()}:",
                file=sys.stderr,
            )
            for d in drifted:
                print(f"  - {d}", file=sys.stderr)
            print("\nRun: python tools/sync-bootstrap-killswitch.py", file=sys.stderr)
        if call_site_gaps:
            ok = False
            print(
                "the following bootstrap-check.* files do not call their vendored "
                "bootstrap-killswitch guard -- the switch has no effect for them:",
                file=sys.stderr,
            )
            for g in call_site_gaps:
                print(f"  - {g}", file=sys.stderr)
            print(
                "\nSee libs/bootstrap-killswitch/README.md for the required call-site "
                "wiring (this tool vendors the guard FILES; it does not auto-wire the "
                "call site, since that lives inside each plugin's own hand-maintained "
                "bootstrap-check.*).",
                file=sys.stderr,
            )
        if not ok:
            return 1
        print(f"bootstrap-killswitch guard files in sync across {scope}.")
        wired_count = sum(1 for p in plugins if p.name not in NO_RECONCILE_STUBS)
        print(f"All {wired_count} wired adopters call their guard.")
        return 0

    if written:
        print(f"Synced bootstrap-killswitch guard files ({len(written)} file(s)):")
        for w in written:
            print(f"  + {w}")
    else:
        print(f"Already in sync across {scope}; nothing to do.")
    if call_site_gaps:
        print(
            "\nWARNING: the following bootstrap-check.* files do not call their "
            "vendored guard (this tool only vendors guard files; it does not "
            "auto-wire the call site):",
            file=sys.stderr,
        )
        for g in call_site_gaps:
            print(f"  - {g}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
