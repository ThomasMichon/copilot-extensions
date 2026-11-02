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

The ordinary POSIX selected-runtime readiness fragment is shared independently
of those deploy-model families. Its Bridge reference is synchronized with
``--sync-readiness``; the default check also detects fragment drift.

Usage::

    python tools/check-bootstrap-sync.py           # verify (CI / pre-push)
    python tools/check-bootstrap-sync.py --list      # show the family grouping
"""
from __future__ import annotations

import argparse
import hashlib
import re
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
        "agent-logger",
    ],
    # These adopters submit their own typed actions to the shared execution owner.
    "versioned-venv/driver-agent-codespaces": ["agent-codespaces"],
    "versioned-venv/driver-agent-pull-requests": ["agent-pull-requests"],
    "versioned-venv/driver-agent-vault": ["agent-vault"],
    # Containers slots also bind its persistent base/provider-exec dependency choice.
    "versioned-venv/dependency-profile-agent-containers": ["agent-containers"],
    # budget-guidance can stamp its payload-local command before Python exists.
    "versioned-venv/pythonless-budget-guidance": ["budget-guidance"],
    # Agent Index keeps the shared versioned-venv model but is the
    # service-bearing installation-cell exemplar. Its bootstrap can inspect an
    # explicitly selected, validated context without making that root operative.
    "versioned-venv/context-selected-agent-index": ["agent-index"],
    # agent-mcp keeps the shared versioned-venv model but is scoped by
    # installation cell (#2225): its bootstrap early-exits when
    # COPILOT_EXTENSIONS_CONTEXT is set, deferring the reconcile decision to
    # the selected cell rather than the plugin root -- the same rationale as
    # agent-index's split above, a genuinely distinct variant, not drift.
    "versioned-venv/context-selected-agent-mcp": ["agent-mcp"],
    # Bridge submits stamp/install; its source-aware POSIX readiness is the
    # shared reference independently of execution-owner classification.
    "versioned-venv/driver-agent-bridge": ["agent-bridge"],
    # Dispatch tracks the actual worker and its process tree, fences completion
    # by attempt, and permits its longer graceful-cutover installer deadline.
    "versioned-venv/agent-dispatch-worker": ["agent-dispatch"],
    # Versioned-venv reconcile that reads the deploy manifest's source.path
    # instead of $PSScriptRoot. Two members that differ from each other in their
    # readiness gate (binstub vs .venv), so each is its own family for now.
    "manifest-path/agent-machines": ["agent-machines"],
    "manifest-path/agent-ssh": ["agent-ssh"],
    # Lightweight lib-copy deploy model (no versioned venv): commit-gated package
    # copy + build-info stamping.
    "lib-copy/agent-worktrees": ["agent-worktrees"],
}

SELECTED_RUNTIME_PLUGINS = (
    "agent-bridge", "agent-dispatch", "agent-mcp",
)
READINESS_START = "# --- selected completed runtime readiness (reference: agent-bridge) ---"
READINESS_END = "# --- end selected completed runtime readiness ---"

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
    reference = PLUGINS_DIR / "agent-bridge" / _HOOK_SH
    readiness = _readiness(reference)
    for plugin in SELECTED_RUNTIME_PLUGINS:
        if readiness is None or _readiness(PLUGINS_DIR / plugin / _HOOK_SH) != readiness:
            problems.append(f"{plugin}: selected-runtime readiness differs from agent-bridge")

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


def _readiness(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if text.count(READINESS_START) != 1 or text.count(READINESS_END) != 1:
        return None
    return text.split(READINESS_START, 1)[1].split(READINESS_END, 1)[0]


def sync_readiness() -> None:
    reference = _readiness(PLUGINS_DIR / "agent-bridge" / _HOOK_SH)
    if reference is None:
        raise ValueError("agent-bridge readiness reference is missing or ambiguous")
    replacement = READINESS_START + reference + READINESS_END
    for plugin in SELECTED_RUNTIME_PLUGINS[1:]:
        path = PLUGINS_DIR / plugin / _HOOK_SH
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f"Refusing linked bootstrap destination: {path}")
        text = path.read_text(encoding="utf-8")
        pattern = (
            re.escape(READINESS_START) + r".*?" + re.escape(READINESS_END)
            if READINESS_START in text else
            r'# "Provisioned" no longer implies a \.venv:.*?'
            r'if \[ "\$provisioned" = 1 \] && \[ "\$deployed" = "\$current" \]; then exit 0; fi'
        )
        updated, count = re.subn(pattern, lambda _: replacement, text, flags=re.DOTALL)
        if count != 1:
            raise ValueError(f"Expected one readiness block: {path}")
        if updated != text:
            path.write_text(updated, encoding="utf-8", newline="\n")


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
    ap.add_argument(
        "--sync-readiness", action="store_true",
        help="copy the agent-bridge selected-runtime predicate to its adopting hook families",
    )
    args = ap.parse_args()

    if args.sync_readiness:
        sync_readiness()
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
