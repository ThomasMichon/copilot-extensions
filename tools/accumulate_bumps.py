#!/usr/bin/env python3
"""Consume pending changefiles (tools/changefile.py) and compute + apply the
real per-plugin version bump -- the mechanical replacement for a contributor
hand-picking a ``-devN`` (the collision hazard in
ThomasMichon/copilot-extensions#182) and hand-editing three files
(docs/pipelines.md's current three-file version contract).

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

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- see uv_editable_ref.py's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
MARKETPLACE = REPO / ".github" / "plugin" / "marketplace.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from changefile import read_changefiles
import uv_editable_ref as uer  # noqa: E402

_VERSION_LITERAL = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?$")
_JSON_VERSION_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')
_TOML_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)
_TOML_TABLE_HEADER_RE = re.compile(r'^[ \t]*\[', re.MULTILINE)

BUMP_ORDER = ("dev", "patch", "minor", "major")

# Plugins whose bump also advances the marketplace catalog's own
# metadata.version, per docs/pipelines.md's "agent-worktrees additionally bumps
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


# Top-level consumer trees outside `plugins/` that still participate in the
# bump/changefile mechanism (e.g. `worktree-manager`) are detected
# dynamically below (`is_standalone_consumer`/`_consumer_root` -- no
# `plugin.json` under `plugins/<consumer>` means it's a standalone,
# out-of-plugin tree) rather than via a duplicated hardcoded list.


def is_standalone_consumer(consumer: str) -> bool:
    """True when ``consumer`` is a RECOGNIZED, top-level, out-of-plugin
    consumer tree (currently just ``worktree-manager``, per
    ``uv_editable_ref._EXTRA_CONSUMER_DIRS``) -- its release version lives
    directly in its own ``pyproject.toml`` ``[project].version`` instead of
    a ``plugin.json``. Deliberately checks membership in that fixed
    registry rather than merely "no plugin.json under
    `plugins/<consumer>`" -- the latter would also match a malformed/typo'd
    changefile name like ``libs/zdd`` or ``plugins/agent-bridge`` (whose
    ``PLUGINS_DIR / consumer`` lookup is not a directory at all), silently
    resolving it through `_consumer_root()` to an unintended real
    `pyproject.toml` elsewhere in the tree and bumping THAT file instead of
    correctly skipping the unrecognized name (PR #4514 review)."""
    return consumer in uer._EXTRA_CONSUMER_DIRS and (REPO / consumer / "pyproject.toml").is_file()


def _consumer_root(consumer: str) -> Path:
    """The consumer's own root dir -- ``plugins/<consumer>`` for an ordinary
    plugin, or the top-level ``<consumer>/`` tree for a standalone one."""
    if (PLUGINS_DIR / consumer).is_dir():
        return PLUGINS_DIR / consumer
    return REPO / consumer


def read_pyproject_project_version(root: Path) -> str | None:
    """The ``[project].version`` field of ``root/pyproject.toml`` --
    genuine TOML parsing, not a "first `version = ` line anywhere in the
    file" regex, so an earlier unrelated table's own ``version`` key (e.g.
    ``[tool.example] version = "9.9.9"``) is never mistaken for the
    package's own release version (PR #4514 review)."""
    pp = root / "pyproject.toml"
    if not pp.exists():
        return None
    try:
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


_TOML_QUOTED_VERSION_RE = re.compile(r"^(\s*version\s*=\s*)([\"'])([^\"']+)\2", re.MULTILINE)


def _write_project_version(pp: Path, new_version: str) -> bool:
    """Rewrite ONLY the ``[project].version`` field of ``pp`` -- scoped to
    that table's own span (mirroring `cell-runtime.py`'s
    `_rewrite_uv_source_to_local()`), never the first `version = ` line
    anywhere in the file, so an earlier unrelated table's own `version` key
    is never silently overwritten instead of the package's real one (PR
    #4514 review). Matches EITHER TOML quote style (`"..."` or `'...'`,
    both valid) and preserves whichever the manifest already used --
    `read_pyproject_project_version()` (genuine `tomllib` parsing) accepts
    both, so a write-side regex that only matched double quotes could
    compute a real bump for a single-quoted manifest and then silently
    fail to apply it, letting the changefile-consuming promotion path ship
    the OLD version with no error at all (PR #4514 review). Also matches
    a quoted table name (`["project"]`/`['project']`), an equally valid
    TOML spelling of the same table (promotion now additionally aborts
    rather than silently consuming a changefile whenever any computed
    bump could not actually be applied -- see `promote_release.py`'s
    `consume_pending_changes()` -- so this class of edge case can no
    longer silently lose a bump even if some future TOML form is still
    missed here)."""
    if not pp.exists():
        return False
    text = pp.read_text(encoding="utf-8")
    header = re.search(r"""^[ \t]*\[\s*(?:project|"project"|'project')\s*\][ \t]*(?:#.*)?$""", text, re.MULTILINE)
    if header is None:
        return False
    start = header.end()
    next_header = _TOML_TABLE_HEADER_RE.search(text, start + 1)
    end = next_header.start() if next_header else len(text)
    new_body, n = _TOML_QUOTED_VERSION_RE.subn(
        lambda m: f"{m.group(1)}{m.group(2)}{new_version}{m.group(2)}", text[start:end], count=1,
    )
    if n == 0:
        return False
    pp.write_text(text[:start] + new_body + text[end:], encoding="utf-8")
    return True


def read_consumer_version(consumer: str) -> str | None:
    """The release version for any consumer `_vendored_consumers()`/
    `pending_bumps()` can name -- a ``plugins/<consumer>`` plugin
    (``plugin.json``) or a standalone, out-of-plugin consumer tree (its own
    ``pyproject.toml`` ``[project].version``). Generalizes
    `read_plugin_json_version` so `compute()`/`apply()` can actually update
    a standalone consumer's version surfaces instead of silently skipping
    it (PR #4465/#4514 review: a shared-lib bump could pass the
    changefile-presence gate while a standalone consumer like
    `worktree-manager` kept its old version)."""
    if is_standalone_consumer(consumer):
        return read_pyproject_project_version(_consumer_root(consumer))
    return read_plugin_json_version(consumer)


def iter_standalone_consumer_names() -> list[str]:
    """Every standalone, out-of-plugin consumer name this module's
    `compute()`/`apply()`/`compute_from_diff()` can bump (e.g.
    `worktree-manager`) -- the single place a caller that needs to
    enumerate them (e.g. `promote_release.py`'s seed-from-main step)
    should look, rather than re-hardcoding
    `uv_editable_ref._EXTRA_CONSUMER_DIRS` a third time."""
    return [name for name in uer._EXTRA_CONSUMER_DIRS if (REPO / name / "pyproject.toml").is_file()]


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


def _write_source_fallbacks(base_dir: Path, old_version: str, new_version: str) -> int:
    """Rewrite literal ``__version__``/``_FALLBACK_VERSION`` fallbacks equal to
    ``old_version`` under ``base_dir/src/*/*.py`` -- ``base_dir`` is a
    plugin's own root (``PLUGINS_DIR / plugin``) or a standalone consumer's
    top-level root (``REPO / consumer``), both laid out the same way."""
    pattern = re.compile(_FALLBACK_ASSIGNMENT + re.escape(old_version) + r'(["\'])', re.MULTILINE)
    written = 0
    for path in sorted((base_dir / "src").glob("*/*.py")):
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
    """Map ``consumer -> (old_version, new_version)`` for every pending
    consumer -- an ordinary ``plugins/*`` plugin or a standalone,
    out-of-plugin consumer tree like ``worktree-manager`` (PR #4465/#4514
    review: `check-changefile-presence.py` already requires a changefile
    for a standalone consumer's own content/vendored-lib changes, but this
    used to only know how to compute a bump for a `plugin.json`-bearing
    plugin, silently skipping every standalone one instead)."""
    result: dict[str, tuple[str, str]] = {}
    for consumer, types in sorted(grouped.items()):
        current = read_consumer_version(consumer)
        if current is None:
            print(f"accumulate-bumps: skipping {consumer} -- no plugin.json/pyproject "
                  "version found", file=sys.stderr)
            continue
        new_version = bump_version(current, highest_bump(types))
        result[consumer] = (current, new_version)
    return result


def apply(result: dict[str, tuple[str, str]]) -> list[str]:
    """Write the computed versions; return the list of successfully-applied consumers."""
    applied: list[str] = []
    for consumer, (old, new_version) in result.items():
        if is_standalone_consumer(consumer):
            # A standalone consumer (e.g. `worktree-manager`) has no
            # `plugin.json` and is never in the marketplace catalog or an
            # instruction-projection owner -- only its own `pyproject.toml`
            # [project].version and source `__version__` fallbacks apply.
            root = _consumer_root(consumer)
            ok_pp = _write_project_version(root / "pyproject.toml", new_version)
            _write_source_fallbacks(root, old, new_version)
            if ok_pp:
                applied.append(consumer)
            else:
                print(
                    f"accumulate-bumps: {consumer} (standalone) failed to write "
                    "pyproject.toml version",
                    file=sys.stderr,
                )
            continue
        pj = PLUGINS_DIR / consumer / "plugin.json"
        pp = PLUGINS_DIR / consumer / "pyproject.toml"
        ok_pj = _write_version(pj, _JSON_VERSION_RE, new_version)
        ok_pp = _write_version(pp, _TOML_VERSION_RE, new_version) if pp.exists() else True
        ok_mkt = _write_marketplace_entry(
            consumer, new_version, also_metadata=consumer in CATALOG_METADATA_PLUGINS
        )
        _write_source_fallbacks(PLUGINS_DIR / consumer, old, new_version)
        _write_instruction_projection_owners(consumer, old, new_version)
        if ok_pj and ok_pp and ok_mkt:
            applied.append(consumer)
        else:
            print(
                f"accumulate-bumps: {consumer} partially applied "
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


def _project_version_at(ref: str, rel_pyproject: str) -> str | None:
    """The ``[project].version`` field of ``rel_pyproject`` at ``ref`` --
    genuine TOML parsing (mirroring `read_pyproject_project_version`), not
    `_version_at`'s generic "first `version = ` line" regex, which could
    read an earlier unrelated table's own `version` key instead of a
    standalone consumer's real release version (PR #4514 review)."""
    text = _git("show", f"{ref}:{rel_pyproject}")
    if not text:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def _next_after_base(current: str, base_version: str | None) -> str | None:
    """The version to bump to, or ``None`` when ``current`` is already ahead of the base."""
    if base_version is None:
        return None  # new on this branch: its first version is the author's call
    if version_key(current) > version_key(base_version):
        return None
    return bump_version(base_version, "dev")


def _changed_libs(changed: list[str]) -> set[str]:
    libs = set()
    standalone_names = set(iter_standalone_consumer_names())
    for path in changed:
        parts = path.split("/")
        if len(parts) >= 5 and parts[0] == "plugins" and parts[2] == "libs" and parts[4] == "src":
            libs.add(parts[3])
        elif len(parts) >= 3 and parts[0] == "libs" and parts[2] == "src":
            libs.add(parts[1])
        elif (
            len(parts) >= 4 and parts[0] in standalone_names
            and parts[1] == "libs" and parts[3] == "src"
        ):
            # A recognized standalone consumer's OWN top-level libs/<lib>/
            # real copy (mirroring worktree-manager/libs/zdd) -- without
            # this, a lib change confined entirely to that copy (no
            # plugin/canonical copy also touched) was invisible here, so
            # lib_bumps_from_diff() never even got a lib name to look the
            # copy up under (PR #4514 review).
            libs.add(parts[2])
    return libs


def lib_bumps_from_diff(base: str, changed: list[str]) -> dict[Path, tuple[str, str]]:
    """Map each copy's ``pyproject.toml`` -> (old, new) for vendored libs whose source changed."""
    result: dict[Path, tuple[str, str]] = {}
    for lib in sorted(_changed_libs(changed)):
        copies = sorted(PLUGINS_DIR.glob(f"*/libs/{lib}/pyproject.toml"))
        # A recognized standalone consumer (e.g. worktree-manager) can also
        # carry its own real vendored copy under its own top-level libs/ --
        # check-vendored-libs-sync.py already includes this shape in its
        # own version-agreement check, so leaving it out of the mechanical
        # --from-diff shortcut would create exactly the version-skew
        # failure that check then flags (PR #4514 review).
        for consumer in iter_standalone_consumer_names():
            candidate = REPO / consumer / "libs" / lib / "pyproject.toml"
            if candidate.is_file():
                copies.append(candidate)
        # The top-level CANONICAL libs/<lib>/pyproject.toml (distinct from
        # any plugin's own vendored copy) is what gets materialized into
        # every pointer-only consumer at promotion time
        # (tools/materialize_main.py) -- for a lib with a mix of real
        # copies and pointer-only consumers (e.g. plugin-resolve), leaving
        # canonical out of this bump means promotion ships its OLD,
        # unbumped content into every pointer consumer, creating version
        # skew on main even though every real copy bumped correctly (PR
        # #4514 review).
        canonical = REPO / "libs" / lib / "pyproject.toml"
        if canonical.is_file():
            copies.append(canonical)
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
    """Consumer and lib bumps this branch needs relative to ``base`` (idempotent)."""
    guard = _version_bump_guard()
    changed = _changed_since(base)
    consumers = guard._vendored_consumers()
    needing = set(guard._plugins_needing_bump(changed, consumers))
    for lib in _changed_libs(changed):  # every copy of a changed lib ships in its consumer
        needing |= set(consumers.get(lib, ()))
    plugins: dict[str, tuple[str, str]] = {}
    for consumer in sorted(needing):
        current = read_consumer_version(consumer)
        if is_standalone_consumer(consumer):
            rel_pyproject = f"{consumer}/pyproject.toml"
            base_version = _project_version_at(base, rel_pyproject)
        else:
            base_version = _version_at(base, f"plugins/{consumer}/plugin.json", _JSON_VERSION_RE)
        new = _next_after_base(current, base_version) if current else None
        if new:
            plugins[consumer] = (current, new)
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
