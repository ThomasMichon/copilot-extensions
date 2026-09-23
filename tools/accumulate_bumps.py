#!/usr/bin/env python3
"""Consume pending changefiles (tools/changefile.py) and compute + apply the
real per-plugin version bump -- the mechanical replacement for a contributor
hand-picking a ``-devN`` (the collision hazard in
ThomasMichon/copilot-extensions#182) and hand-editing three files
(CONTRIBUTING.md's current three-file version contract).

Version scheme: ``MAJOR.MINOR.PATCH-devN`` (matches this repo's existing
convention). A ``major``/``minor``/``patch`` changefile resets the lower
segments and starts a fresh ``-dev1``; a ``dev`` changefile only advances the
``devN`` counter. When several pending changefiles target the same plugin
with different types, the *largest* type wins (major > minor > patch > dev)
-- matching beachball's own "biggest requested bump wins" semantics.

Usage::

    python tools/accumulate_bumps.py --dry-run   # show computed bumps, change nothing
    python tools/accumulate_bumps.py --apply     # write plugin.json / pyproject.toml /
                                                  # marketplace.json, then remove the
                                                  # consumed changefiles
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
MARKETPLACE = REPO / ".github" / "plugin" / "marketplace.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from changefile import read_changefiles

_VERSION_LITERAL = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?$")
_JSON_VERSION_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')
_TOML_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)

BUMP_ORDER = ("dev", "patch", "minor", "major")

# Plugins whose bump also advances the marketplace catalog's own
# metadata.version, per CONTRIBUTING.md's "agent-worktrees additionally bumps
# metadata.version" rule.
CATALOG_METADATA_PLUGINS = frozenset({"agent-worktrees"})


class VersionError(ValueError):
    pass


def parse_version(v: str) -> tuple[int, int, int, int | None]:
    m = _VERSION_LITERAL.match(v)
    if not m:
        raise VersionError(f"unparseable version: {v!r}")
    major, minor, patch, dev = m.groups()
    return int(major), int(minor), int(patch), (int(dev) if dev else None)


def bump_version(current: str, bump_type: str) -> str:
    major, minor, patch, dev = parse_version(current)
    if bump_type == "major":
        major, minor, patch, dev = major + 1, 0, 0, 1
    elif bump_type == "minor":
        minor, patch, dev = minor + 1, 0, 1
    elif bump_type == "patch":
        patch, dev = patch + 1, 1
    elif bump_type == "dev":
        dev = (dev or 0) + 1
    else:
        raise VersionError(f"unknown bump type: {bump_type!r}")
    return f"{major}.{minor}.{patch}-dev{dev}"


def highest_bump(types: list[str]) -> str:
    return max(types, key=BUMP_ORDER.index)


def pending_bumps() -> dict[str, list[str]]:
    """Map ``plugin -> [requested bump types]`` across every pending changefile."""
    grouped: dict[str, list[str]] = {}
    for _path, data in read_changefiles():
        for change in data.get("changes", []):
            grouped.setdefault(change["plugin"], []).append(change["type"])
    return grouped


def _read_plugin_json_version(plugin: str) -> str | None:
    pj = PLUGINS_DIR / plugin / "plugin.json"
    if not pj.exists():
        return None
    m = _JSON_VERSION_RE.search(pj.read_text(encoding="utf-8"))
    return m.group(2) if m else None


def _write_version(path: Path, pattern: re.Pattern[str], new_version: str, *, count: int = 1) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new_text, n = pattern.subn(rf"\g<1>{new_version}\g<3>", text, count=count)
    if n == 0:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def _write_marketplace_entry(plugin: str, new_version: str, *, also_metadata: bool) -> bool:
    if not MARKETPLACE.exists():
        return False
    text = MARKETPLACE.read_text(encoding="utf-8")
    # Scope the version replacement to this plugin's own object in the
    # `plugins` array by anchoring on its `"name": "<plugin>"` line first.
    block_re = re.compile(
        r'(\{\s*"name"\s*:\s*"' + re.escape(plugin) + r'".*?"version"\s*:\s*")([^"]+)(")',
        re.DOTALL,
    )
    new_text, n = block_re.subn(rf"\g<1>{new_version}\g<3>", text, count=1)
    if n == 0:
        return False
    if also_metadata:
        meta_re = re.compile(r'("metadata"\s*:\s*\{[^{}]*?"version"\s*:\s*")([^"]+)(")', re.DOTALL)
        new_text, meta_n = meta_re.subn(rf"\g<1>{new_version}\g<3>", new_text, count=1)
        if meta_n == 0:
            return False
    MARKETPLACE.write_text(new_text, encoding="utf-8")
    return True


def compute(grouped: dict[str, list[str]]) -> dict[str, tuple[str, str]]:
    """Map ``plugin -> (old_version, new_version)`` for every pending plugin."""
    result: dict[str, tuple[str, str]] = {}
    for plugin, types in sorted(grouped.items()):
        current = _read_plugin_json_version(plugin)
        if current is None:
            print(f"accumulate-bumps: skipping {plugin} -- no plugin.json/version found",
                  file=sys.stderr)
            continue
        new_version = bump_version(current, highest_bump(types))
        result[plugin] = (current, new_version)
    return result


def apply(result: dict[str, tuple[str, str]]) -> list[str]:
    """Write the computed versions; return the list of successfully-applied plugins."""
    applied: list[str] = []
    for plugin, (_old, new_version) in result.items():
        pj = PLUGINS_DIR / plugin / "plugin.json"
        pp = PLUGINS_DIR / plugin / "pyproject.toml"
        ok_pj = _write_version(pj, _JSON_VERSION_RE, new_version)
        ok_pp = _write_version(pp, _TOML_VERSION_RE, new_version) if pp.exists() else True
        ok_mkt = _write_marketplace_entry(
            plugin, new_version, also_metadata=plugin in CATALOG_METADATA_PLUGINS
        )
        if ok_pj and ok_pp and ok_mkt:
            applied.append(plugin)
        else:
            print(
                f"accumulate-bumps: {plugin} partially applied "
                f"(plugin.json={ok_pj} pyproject={ok_pp} marketplace={ok_mkt})",
                file=sys.stderr,
            )
    return applied


def _consume_changefiles() -> None:
    for path, _data in read_changefiles():
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="show computed bumps only (default)")
    mode.add_argument("--apply", action="store_true",
                       help="write versions and remove consumed changefiles")
    args = ap.parse_args(argv)

    grouped = pending_bumps()
    if not grouped:
        print("accumulate-bumps: no pending changefiles.")
        return 0

    result = compute(grouped)
    for plugin, (old, new) in sorted(result.items()):
        print(f"{plugin}: {old} -> {new}")

    if args.apply:
        applied = apply(result)
        _consume_changefiles()
        print(f"accumulate-bumps: applied {len(applied)}/{len(result)} bump(s); "
              "changefiles consumed.")
        return 0 if len(applied) == len(result) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
