#!/usr/bin/env python3
"""Require a version bump whenever a plugin's content changes (CONTRIBUTING.md
§ Release & Versioning).

The marketplace only redeploys a plugin's runtime when its declared version
**advances**: `<repo> update` refreshes the payload but the versioned-runtime
install is version-gated, so new code shipped under an unchanged version silently
serves stale (dotfiles #1025). The consistency guard
(`check-version-consistency.py`) proves a plugin's version is *identical* across
its files, but it is silent when the version doesn't move at all. This guard
closes that hole: **touch a plugin's content -> bump its version.**

What requires a bump, for a push/PR diff (`<base>..HEAD`):

* **Any file under `plugins/<p>/`** (its `src/`, `skills/`, `agents/`, its own
  `docs/`, tests, manifests -- everything ships or informs downstream agents) =>
  `<p>`'s `plugin.json` `version` must differ from the base.
* **Any file under a top-level, shared `libs/<lib>/`** (the canonical source that
  is *vendored* into plugins) => **every plugin that vendors `<lib>`** must bump.
  A shared-lib change reaches every consumer, so each consumer's payload changes
  (see `check-vendored-libs-sync.py`, dotfiles #929).

What does **not** require a bump: repo-root files that are not vendored into any
plugin -- `tools/`, `.github/`, the repo-root `docs/`, `CONTRIBUTING.md`,
`README.md`, etc. (CONTRIBUTING.md § Version scheme).

Scope is the push/PR diff only (like `check-no-internal-identifiers.py`), so a
pre-existing un-bumped state in an untouched plugin never blocks an unrelated
push. Newly added or deleted plugins are skipped (no before/after version to
compare).

Usage::

    python tools/check-version-bump.py                 # diff vs origin/main (pre-push/CI)
    python tools/check-version-bump.py --base <sha>     # diff vs an explicit base
    python tools/check-version-bump.py --list           # show the plugin<->vendored-lib map

Exit code 0 = conformant (or nothing to check), 1 = a touched plugin didn't bump.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"

# Build/artifact noise under a plugin dir that never counts as "content".
_IGNORE_PARTS = {"build", "dist", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
_IGNORE_SUFFIX = {".pyc", ".pyo"}

_PLUGIN_JSON_VERSION = re.compile(r'"version"\s*:\s*"([^"]+)"')


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=False,
    )


def _rev_parse(ref: str) -> str | None:
    r = _git("rev-parse", "--verify", "--quiet", ref)
    out = r.stdout.strip()
    return out or None


def _merge_base(base: str, head: str) -> str | None:
    r = _git("merge-base", base, head)
    return r.stdout.strip() or None


def _changed_files(base: str, head: str) -> list[str]:
    r = _git("diff", "--name-only", f"{base}..{head}")
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _plugin_json_version_at(ref: str, plugin: str) -> str | None:
    """The ``version`` field of ``plugins/<plugin>/plugin.json`` at ``ref``.

    ``None`` when the file does not exist at that ref (a plugin added or removed
    across the range) or carries no version -- callers skip those."""
    r = _git("show", f"{ref}:plugins/{plugin}/plugin.json")
    if r.returncode != 0:
        return None
    m = _PLUGIN_JSON_VERSION.search(r.stdout)
    return m.group(1) if m else None


def _vendored_consumers() -> dict[str, list[str]]:
    """Map ``lib name -> [plugins that vendor it]`` from ``plugins/*/libs/*``."""
    consumers: dict[str, list[str]] = {}
    if not PLUGINS_DIR.is_dir():
        return consumers
    for plugin in sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir()):
        libs = plugin / "libs"
        if not libs.is_dir():
            continue
        for lib in sorted(x for x in libs.iterdir() if x.is_dir()):
            consumers.setdefault(lib.name, []).append(plugin.name)
    return consumers


def _is_ignored(rel_parts: tuple[str, ...], name: str) -> bool:
    if _IGNORE_PARTS & set(rel_parts):
        return True
    return any(name.endswith(s) for s in _IGNORE_SUFFIX)


def _plugins_needing_bump(changed: list[str], consumers: dict[str, list[str]]) -> dict[str, set[str]]:
    """Map ``plugin -> {reasons}`` for every plugin whose content changed.

    A path under ``plugins/<p>/`` charges ``<p>``; a path under a top-level
    shared ``libs/<lib>/`` charges every plugin that vendors ``<lib>``."""
    needing: dict[str, set[str]] = {}
    for path in changed:
        parts = tuple(path.split("/"))
        if len(parts) < 2:
            continue
        if parts[0] == "plugins":
            plugin = parts[1]
            inner = parts[2:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            # Only real plugin dirs (with a manifest) count.
            if (PLUGINS_DIR / plugin / "plugin.json").exists():
                needing.setdefault(plugin, set()).add(f"plugins/{plugin}/")
        elif parts[0] == "libs" and len(parts) >= 2:
            lib = parts[1]
            inner = parts[2:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            for plugin in consumers.get(lib, ()):
                needing.setdefault(plugin, set()).add(f"libs/{lib}/ (vendored)")
    return needing


def check(base_ref: str, head_ref: str) -> tuple[int, list[str]]:
    head = _rev_parse(head_ref)
    if head is None:
        # Nothing resolvable to check (e.g. an empty repo) -- never block.
        print(f"check-version-bump: cannot resolve HEAD ({head_ref}); skipping.")
        return 0, []
    base = _rev_parse(base_ref)
    if base is None:
        # The base (default origin/main) is unavailable -- a fresh clone or a
        # detached state. Degrade to a no-op rather than wedge the push.
        print(
            f"check-version-bump: base '{base_ref}' unavailable; skipping "
            "(fetch it to enable the guard).",
        )
        return 0, []
    mbase = _merge_base(base, head) or base

    changed = _changed_files(mbase, head)
    if not changed:
        print("check-version-bump: no changes vs base; nothing to check.")
        return 0, []

    consumers = _vendored_consumers()
    needing = _plugins_needing_bump(changed, consumers)
    if not needing:
        print("check-version-bump: no plugin content touched; nothing to bump.")
        return 0, []

    violations: list[str] = []
    for plugin in sorted(needing):
        head_ver = _plugin_json_version_at(head, plugin)
        base_ver = _plugin_json_version_at(mbase, plugin)
        if head_ver is None or base_ver is None:
            # Plugin added or removed across the range -- no bump obligation.
            continue
        if head_ver == base_ver:
            reasons = ", ".join(sorted(needing[plugin]))
            violations.append(
                f"{plugin}: content changed ({reasons}) but version is still "
                f"{head_ver} -- bump it (plugin.json + pyproject.toml + "
                "marketplace.json, per CONTRIBUTING.md)."
            )
    return (1 if violations else 0), violations


def _print_list() -> None:
    consumers = _vendored_consumers()
    shared = {lib: plugs for lib, plugs in consumers.items() if len(plugs) >= 1}
    print("Vendored shared libs -> consuming plugins (a lib change bumps them all):")
    for lib in sorted(shared):
        top = "canonical" if (LIBS_DIR / lib).is_dir() else "no top-level source"
        print(f"  libs/{lib}  ({top}) -> {', '.join(shared[lib])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main",
                    help="base ref to diff against (default: origin/main)")
    ap.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    ap.add_argument("--list", action="store_true",
                    help="print the plugin<->vendored-lib map and exit")
    args = ap.parse_args(argv)

    if args.list:
        _print_list()
        return 0

    code, violations = check(args.base, args.head)
    if violations:
        print("check-version-bump: FAILED", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nEvery plugin whose content changes must bump its version so the "
            "marketplace redeploys it (an un-bumped change serves stale, "
            "dotfiles #1025). Use a patch `-devN` bump (CONTRIBUTING.md § "
            "'Default: bump patch with -devN'). A shared `libs/<lib>` change "
            "must bump every plugin that vendors it.",
            file=sys.stderr,
        )
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
