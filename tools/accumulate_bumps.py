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
    python tools/accumulate_bumps.py --from-diff origin/main --apply
        # no changefiles: bump exactly what check-version-bump requires for this
        # branch vs the base -- every touched plugin, every plugin that vendors a
        # changed lib, and the lib itself in all its copies -- each only when it
        # is not already ahead of the base's tip (safe to re-run after a rebase)

Every bump also rewrites the plugin's literal ``__version__`` /
``_FALLBACK_VERSION`` source fallbacks, which check-version-consistency
requires to agree with plugin.json.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
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


def version_key(v: str) -> tuple[int, int, int, float]:
    """Order versions; a release sorts after every ``-devN`` of the same triple."""
    major, minor, patch, dev = parse_version(v)
    return major, minor, patch, float("inf") if dev is None else dev


def highest_bump(types: list[str]) -> str:
    return max(types, key=BUMP_ORDER.index)


def pending_bumps() -> dict[str, list[str]]:
    """Map ``plugin -> [requested bump types]`` across every pending changefile."""
    grouped: dict[str, list[str]] = {}
    for _path, data in read_changefiles():
        for change in data.get("changes", []):
            grouped.setdefault(change["plugin"], []).append(change["type"])
    return grouped


def read_plugin_json_version(plugin: str) -> str | None:
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


_FALLBACK_ASSIGNMENT = r'^(\s*(?:__version__|_FALLBACK_VERSION)\s*(?::\s*str\s*)?=\s*["\'])'


def _write_source_fallbacks(plugin: str, old_version: str, new_version: str) -> int:
    """Rewrite literal ``__version__``/``_FALLBACK_VERSION`` fallbacks equal to ``old_version``."""
    pattern = re.compile(_FALLBACK_ASSIGNMENT + re.escape(old_version) + r'(["\'])', re.MULTILINE)
    written = 0
    for path in sorted((PLUGINS_DIR / plugin / "src").glob("*/*.py")):
        if path.name not in {"__init__.py", "_build_info.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        new_text, n = pattern.subn(rf"\g<1>{new_version}\g<2>", text)
        if n:
            path.write_text(new_text, encoding="utf-8")
            written += n
    return written


def _write_instruction_projection_owners(plugin: str, old_version: str, new_version: str) -> int:
    """Rewrite a literal ``[owner: <plugin>@<old_version>]`` tag to the new
    version inside every instruction-projection TEMPLATE this plugin
    declares (``instruction-projections.json``'s own ``template`` paths).

    This is a source surface no other bump-application step here touches:
    a projection's rendered destination is a derived copy of its template
    (regenerated separately by
    ``plugins/customizing-copilot/skills/reviewing-customizations/scripts/
    manage-instruction-projections.py sync``), but the template itself is a
    checked-in, hand-authored file whose body text embeds the owning
    plugin's version verbatim -- nothing else keeps that string in lockstep
    with plugin.json, so it silently drifted on every real bump until this
    existed (confirmed live on `main`, ThomasMichon/copilot-extensions#3378
    recurrence, 2026-09-25). Fixing the template here is what makes a
    subsequent projection-sync pass (tools/promote_release.py) actually
    correct -- sync alone only re-renders a destination FROM its template,
    faithfully propagating whatever version string the template itself
    still says."""
    declaration_path = PLUGINS_DIR / plugin / "instruction-projections.json"
    if not declaration_path.exists():
        return 0
    try:
        declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    projections = declaration.get("projections")
    if not isinstance(projections, list):
        return 0
    old_tag = f"[owner: {plugin}@{old_version}]"
    new_tag = f"[owner: {plugin}@{new_version}]"
    written = 0
    for entry in projections:
        if not isinstance(entry, dict):
            continue
        template_rel = entry.get("template")
        if not isinstance(template_rel, str):
            continue
        template_path = PLUGINS_DIR / plugin / template_rel
        if not template_path.exists():
            continue
        text = template_path.read_text(encoding="utf-8")
        if old_tag not in text:
            continue
        template_path.write_text(text.replace(old_tag, new_tag), encoding="utf-8")
        written += 1
    return written


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
        current = read_plugin_json_version(plugin)
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
    for plugin, (old, new_version) in result.items():
        pj = PLUGINS_DIR / plugin / "plugin.json"
        pp = PLUGINS_DIR / plugin / "pyproject.toml"
        ok_pj = _write_version(pj, _JSON_VERSION_RE, new_version)
        ok_pp = _write_version(pp, _TOML_VERSION_RE, new_version) if pp.exists() else True
        ok_mkt = _write_marketplace_entry(
            plugin, new_version, also_metadata=plugin in CATALOG_METADATA_PLUGINS
        )
        _write_source_fallbacks(plugin, old, new_version)
        _write_instruction_projection_owners(plugin, old, new_version)
        if ok_pj and ok_pp and ok_mkt:
            applied.append(plugin)
        else:
            print(
                f"accumulate-bumps: {plugin} partially applied "
                f"(plugin.json={ok_pj} pyproject={ok_pp} marketplace={ok_mkt})",
                file=sys.stderr,
            )
    return applied


# --- --from-diff: derive the required bumps from the branch itself ----------

def _version_bump_guard():
    """tools/check-version-bump.py, the single definition of what needs a bump."""
    spec = importlib.util.spec_from_file_location(
        "check_version_bump", Path(__file__).resolve().parent / "check-version-bump.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _changed_since(base: str) -> list[str]:
    """Committed + uncommitted + untracked paths changed since the merge base."""
    merge_base = _git("merge-base", base, "HEAD").strip() or base
    paths = set(_git("diff", "--name-only", merge_base).split())
    paths |= set(_git("ls-files", "--others", "--exclude-standard").split())
    return sorted(paths)


def _version_at(ref: str, rel_path: str, pattern: re.Pattern[str]) -> str | None:
    text = _git("show", f"{ref}:{rel_path}")
    m = pattern.search(text) if text else None
    return m.group(2) if m else None


def _next_after_base(current: str, base_version: str | None) -> str | None:
    """The version to bump to, or ``None`` when ``current`` is already ahead of the base."""
    if base_version is None:
        return None  # new on this branch: its first version is the author's call
    if version_key(current) > version_key(base_version):
        return None
    return bump_version(base_version, "dev")


def _changed_libs(changed: list[str]) -> set[str]:
    libs = set()
    for path in changed:
        parts = path.split("/")
        if len(parts) >= 5 and parts[0] == "plugins" and parts[2] == "libs" and parts[4] == "src":
            libs.add(parts[3])
        elif len(parts) >= 3 and parts[0] == "libs" and parts[2] == "src":
            libs.add(parts[1])
    return libs


def lib_bumps_from_diff(base: str, changed: list[str]) -> dict[Path, tuple[str, str]]:
    """Map each copy's ``pyproject.toml`` -> (old, new) for vendored libs whose source changed."""
    result: dict[Path, tuple[str, str]] = {}
    for lib in sorted(_changed_libs(changed)):
        copies = sorted(PLUGINS_DIR.glob(f"*/libs/{lib}/pyproject.toml"))
        if not copies:
            continue
        current = max(
            (m.group(2) for c in copies if (m := _TOML_VERSION_RE.search(c.read_text(encoding="utf-8")))),
            key=version_key, default=None,
        )
        base_version = max(
            (v for c in copies if (v := _version_at(base, c.relative_to(REPO).as_posix(), _TOML_VERSION_RE))),
            key=version_key, default=None,
        )
        new = _next_after_base(current, base_version) if current else None
        for copy in copies:
            if new:
                result[copy] = (current, new)
    return result


def compute_from_diff(base: str) -> tuple[dict[str, tuple[str, str]], dict[Path, tuple[str, str]]]:
    """Plugin and lib bumps this branch needs relative to ``base`` (idempotent)."""
    guard = _version_bump_guard()
    changed = _changed_since(base)
    consumers = guard._vendored_consumers()
    needing = set(guard._plugins_needing_bump(changed, consumers))
    for lib in _changed_libs(changed):  # every copy of a changed lib ships in its plugin
        needing |= set(consumers.get(lib, ()))
    plugins: dict[str, tuple[str, str]] = {}
    for plugin in sorted(needing):
        current = read_plugin_json_version(plugin)
        base_version = _version_at(base, f"plugins/{plugin}/plugin.json", _JSON_VERSION_RE)
        new = _next_after_base(current, base_version) if current else None
        if new:
            plugins[plugin] = (current, new)
    return plugins, lib_bumps_from_diff(base, changed)


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
    ap.add_argument("--from-diff", metavar="BASE", default=None,
                    help="ignore changefiles; bump what this branch needs vs BASE (e.g. origin/main)")
    args = ap.parse_args(argv)

    if args.from_diff:
        plugins, libs = compute_from_diff(args.from_diff)
        for plugin, (old, new) in plugins.items():
            print(f"{plugin}: {old} -> {new}")
        for path, (old, new) in libs.items():
            print(f"{path.relative_to(REPO).as_posix()}: {old} -> {new}")
        if not plugins and not libs:
            print(f"accumulate-bumps: everything this branch touches is already ahead of {args.from_diff}.")
            return 0
        if not args.apply:
            return 0
        for path, (_old, new) in libs.items():
            _write_version(path, _TOML_VERSION_RE, new)
        applied = apply(plugins)
        print(f"accumulate-bumps: applied {len(applied)}/{len(plugins)} plugin bump(s), "
              f"{len(libs)} lib cop(ies).")
        return 0 if len(applied) == len(plugins) else 1

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
