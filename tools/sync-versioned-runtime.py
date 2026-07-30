#!/usr/bin/env python3
"""Fan the canonical versioned_runtime.py out to every Python runtime plugin.

The immutable-versioned runtime primitive (dotfiles #581) must physically ship
inside each plugin (`scripts/versioned_runtime.py`) because plugins are pulled
**independently** from the marketplace -- it cannot be a shared runtime import.
To keep one source of truth, the canonical copy lives at
``libs/versioned-runtime/versioned_runtime.py`` and this script vendors it,
**byte-identically**, into every Python runtime plugin's ``scripts/`` dir.

Usage::

    python tools/sync-versioned-runtime.py          # copy canonical -> plugins
    python tools/sync-versioned-runtime.py --check   # verify in sync (CI/pre-push)

``--check`` writes nothing and exits non-zero if any plugin copy is missing or
drifted (the same invariant ``tools/check-install-contract.py`` enforces, with a
nudge to run this script). A "Python runtime plugin" is one that ships a
``pyproject.toml`` **and** a runtime installer (``install.*`` or ``init.*``) --
the exact set the install contract requires to carry the primitive.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
CANONICAL = REPO / "libs" / "versioned-runtime" / "versioned_runtime.py"
VRT_NAME = "versioned_runtime.py"


def _has_installer(plugin: Path) -> bool:
    scripts = plugin / "scripts"
    return any(
        (scripts / f"{base}.{ext}").exists()
        for base in ("install", "init")
        for ext in ("ps1", "sh")
    )


def _runtime_plugins() -> list[Path]:
    """Python runtime plugins: a pyproject.toml + a runtime installer."""
    return sorted(
        p for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and (p / "pyproject.toml").exists() and _has_installer(p)
    )


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="verify every plugin copy matches the canonical; write nothing",
    )
    args = ap.parse_args()

    if not CANONICAL.exists():
        print(f"canonical missing: {CANONICAL.relative_to(REPO)}", file=sys.stderr)
        return 1
    canonical_bytes = CANONICAL.read_bytes()
    canonical_sha = _sha(canonical_bytes)

    plugins = _runtime_plugins()
    if not plugins:
        print("No Python runtime plugins found.", file=sys.stderr)
        return 1

    drifted: list[str] = []
    written: list[str] = []
    for plugin in plugins:
        dest = plugin / "scripts" / VRT_NAME
        current = dest.read_bytes() if dest.exists() else None
        if current is not None and _sha(current) == canonical_sha:
            continue
        rel = dest.relative_to(REPO).as_posix()
        if args.check:
            drifted.append(f"{rel} ({'missing' if current is None else 'drifted'})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(canonical_bytes)
        written.append(rel)

    if args.check:
        if drifted:
            print(
                "versioned_runtime.py is out of sync with the canonical source "
                f"({CANONICAL.relative_to(REPO).as_posix()}):",
                file=sys.stderr,
            )
            for d in drifted:
                print(f"  - {d}", file=sys.stderr)
            print("\nRun: python tools/sync-versioned-runtime.py", file=sys.stderr)
            return 1
        print(f"versioned_runtime.py in sync across {len(plugins)} plugins.")
        return 0

    if written:
        print(f"Synced versioned_runtime.py to {len(written)} plugin(s):")
        for w in written:
            print(f"  + {w}")
    else:
        print(f"Already in sync across {len(plugins)} plugins; nothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
