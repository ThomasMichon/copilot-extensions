#!/usr/bin/env python3
"""Guard the bootstrap-check session-start hook against accidental drift (#167).

Every runtime plugin ships ``scripts/bootstrap-check.{ps1,sh}`` -- the
session-start hook that auto-reconciles a stale runtime after a
``copilot plugin update``. These files were once described as "byte-identical
across agent-* runtime plugins", but that is no longer true and *should not* be
forced: the plugins fall into a few genuinely different **deploy-model families**
(a versioned-venv reconcile that locates its source from ``$PSScriptRoot``, one
that reads the deploy manifest's ``source.path``, and a lib-copy model). Forcing
one byte-identical template would break real behavior.

So instead of asserting global byte-identity, this check freezes the intentional
**classification**: it declares which plugins belong to which family and verifies
that

* every runtime plugin (one that ships a ``bootstrap-check.ps1``) is classified
  in exactly one family, and
* every plugin within a multi-member family is byte-identical (both ``.ps1`` and
  ``.sh``) to its siblings.

That catches the drift that actually bites -- someone editing one member of a
shared family but not the others (e.g. the ``.venv`` vs ``venv`` guard fix that
had to be hand-propagated) -- and forces a *conscious* FAMILIES edit when a new
plugin or a new variant is introduced, while permitting the intentional
divergence. Run with ``--list`` to print the current grouping.

Usage::

    python tools/check-bootstrap-sync.py           # verify (CI / pre-push)
    python tools/check-bootstrap-sync.py --list      # show the family grouping
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

# Intentional deploy-model families. Each plugin listed here is expected to ship
# a bootstrap-check; members of a multi-plugin family MUST stay byte-identical.
# A singleton family documents a deliberately distinct variant. Adding a runtime
# plugin (or splitting a variant) is a conscious edit here.
FAMILIES: dict[str, list[str]] = {
    # Versioned-venv reconcile that locates its plugin source from $PSScriptRoot
    # and gates on a .venv link. The shared, unified template.
    "versioned-venv/psscriptroot": [
        "agent-codespaces",
        "agent-containers",
        "agent-dispatch",
        "agent-logger",
        "agent-mcp",
        "agent-vault",
    ],
    # budget-guidance can stamp its payload-local command before Python exists.
    "versioned-venv/pythonless-budget-guidance": ["budget-guidance"],
    # Agent Index keeps the shared versioned-venv model but is the
    # service-bearing installation-cell exemplar. Its bootstrap can inspect an
    # explicitly selected, validated context without making that root operative.
    "versioned-venv/context-selected-agent-index": ["agent-index"],
    # agent-bridge reference: the psscriptroot model plus reconcile observability
    # (reconcile.log / reconcile-status.json) and a venv-or-.venv guard (its
    # stable link is 'venv', not '.venv'). Kept distinct until the observability
    # is propagated to the shared template.
    "versioned-venv/agent-bridge-reference": ["agent-bridge"],
    # Versioned-venv reconcile that reads the deploy manifest's source.path
    # instead of $PSScriptRoot. Two members that differ from each other in their
    # readiness gate (binstub vs .venv), so each is its own family for now.
    "manifest-path/agent-machines": ["agent-machines"],
    "manifest-path/agent-ssh": ["agent-ssh"],
    # Lightweight lib-copy deploy model (no versioned venv): commit-gated package
    # copy + build-info stamping.
    "lib-copy/agent-worktrees": ["agent-worktrees"],
}

_HOOK_PS1 = "scripts/bootstrap-check.ps1"
_HOOK_SH = "scripts/bootstrap-check.sh"


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _runtime_plugins() -> list[str]:
    """Plugins that ship a bootstrap-check.ps1 (the set that must be classified)."""
    return sorted(
        p.name
        for p in PLUGINS_DIR.iterdir()
        if p.is_dir() and (p / _HOOK_PS1).exists()
    )


def verify() -> list[str]:
    """Return a list of human-readable problems; empty means the check passes."""
    problems: list[str] = []

    classified: dict[str, str] = {}
    for family, members in FAMILIES.items():
        for plugin in members:
            if plugin in classified:
                problems.append(
                    f"{plugin}: listed in two families "
                    f"({classified[plugin]} and {family})"
                )
            classified[plugin] = family

    discovered = set(_runtime_plugins())
    declared = set(classified)

    for plugin in sorted(discovered - declared):
        problems.append(
            f"{plugin}: ships a bootstrap-check but is not classified in FAMILIES "
            "-- add it to the right deploy-model family (or a new one)"
        )
    for plugin in sorted(declared - discovered):
        problems.append(
            f"{plugin}: classified in FAMILIES but ships no {_HOOK_PS1} "
            "-- remove it from FAMILIES"
        )

    # Within each multi-member family, every member's hook must be identical.
    for family, members in FAMILIES.items():
        present = [m for m in members if m in discovered]
        if len(present) < 2:
            continue
        for hook in (_HOOK_PS1, _HOOK_SH):
            hashes = {m: _sha(PLUGINS_DIR / m / hook) for m in present}
            distinct = {h for h in hashes.values() if h is not None}
            if len(distinct) > 1:
                by_hash: dict[str, list[str]] = {}
                for m, h in hashes.items():
                    by_hash.setdefault(h or "MISSING", []).append(m)
                groups = "; ".join(
                    f"[{', '.join(sorted(ms))}]={h[:8] if h != 'MISSING' else h}"
                    for h, ms in sorted(by_hash.items())
                )
                problems.append(
                    f"family '{family}' has drifted on {hook}: {groups} "
                    "-- members of one family must stay byte-identical"
                )
            missing = [m for m, h in hashes.items() if h is None]
            if missing:
                problems.append(
                    f"family '{family}' members missing {hook}: "
                    f"{', '.join(sorted(missing))}"
                )
    return problems


def _print_list() -> None:
    for family, members in FAMILIES.items():
        print(f"{family}:")
        for m in members:
            h = _sha(PLUGINS_DIR / m / _HOOK_PS1)
            tag = h[:8] if h else "MISSING"
            print(f"    {m:<20} {tag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--list", action="store_true", help="print the family grouping and exit"
    )
    args = ap.parse_args()

    if args.list:
        _print_list()
        return 0

    problems = verify()
    if problems:
        print("check-bootstrap-sync: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nFix the drift (re-sync the family's members) or, if the change is "
            "intentional, update FAMILIES in tools/check-bootstrap-sync.py.",
            file=sys.stderr,
        )
        return 1
    print(f"check-bootstrap-sync: OK ({len(_runtime_plugins())} runtime plugins, "
          f"{len(FAMILIES)} families).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
