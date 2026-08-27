#!/usr/bin/env python3
"""Enforce that each plugin's version is identical across all files that carry it.

For every plugin ``<p>`` the version must agree across:
  1. ``plugins/<p>/plugin.json``            -> ``version``           (always)
  2. ``plugins/<p>/pyproject.toml``         -> ``[project].version`` (runtime
     plugins only; payload-only plugins have no pyproject and are skipped for
     this file)
  3. ``.github/plugin/marketplace.json``    -> the ``plugins[]`` entry matched
     **by name** -> ``version``             (always)
  4. checked-in source fallbacks under ``plugins/<p>/src/``:
     ``_build_info.py`` ``__version__`` assignments and numeric development
     literals assigned to ``__version__`` or ``_FALLBACK_VERSION``

Why this guard exists: a version bump that touches only one file (e.g. #65
bumped pyproject.toml to dev219 but left plugin.json/marketplace.json at dev218)
creates a permanent, self-perpetuating "Update available" in the Worktree
Picker. The picker's drift check compares the *deployed* runtime version
(stamped from pyproject) against the *payload* version (read from plugin.json);
when they disagree it reports ``venv_drift`` forever and re-applying never
converges. Keeping the triplet in lockstep is the invariant that prevents it.

Run manually:  python tools/check-version-consistency.py
Exit code 0 = conformant, 1 = violations (suitable for a pre-push hook).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
MARKETPLACE = REPO / ".github" / "plugin" / "marketplace.json"

# [project].version = "x.y.z-devN" in a pyproject.toml. Matches the same simple
# scheme the installer uses (scripts/install.ps1) rather than a full TOML parse
# so the guard has no third-party dependency.
_PYPROJECT_VERSION = re.compile(
    r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE
)
_DEV_VERSION = re.compile(r"^\d+\.\d+\.\d+-dev\d+$")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pyproject_version(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    m = _PYPROJECT_VERSION.search(text)
    return m.group(1) if m else None


def _source_fallback_versions(
    path: Path,
) -> tuple[dict[str, str], str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}, "cannot read file"
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, "cannot parse file"
    versions: dict[str, str] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if (
            value is not None
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and _DEV_VERSION.fullmatch(value.value)
        ):
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id in {"__version__", "_FALLBACK_VERSION"}
                ):
                    versions[target.id] = value.value
    return versions, None


def main() -> int:
    mkt = _read_json(MARKETPLACE)
    if not mkt or not isinstance(mkt.get("plugins"), list):
        print(f"check-version-consistency: cannot read {MARKETPLACE}", file=sys.stderr)
        return 1

    mkt_versions = {
        entry["name"]: entry.get("version")
        for entry in mkt["plugins"]
        if isinstance(entry, dict) and entry.get("name")
    }

    violations: list[str] = []
    for plugin_dir in sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir()):
        name = plugin_dir.name
        pj = _read_json(plugin_dir / "plugin.json")
        if pj is None:
            # Not a plugin folder (no manifest); skip.
            continue

        sources: dict[str, str] = {}
        pj_ver = pj.get("version")
        if pj_ver:
            sources["plugin.json"] = pj_ver

        pyproj = plugin_dir / "pyproject.toml"
        if pyproj.exists():
            py_ver = _pyproject_version(pyproj)
            if py_ver:
                sources["pyproject.toml"] = py_ver
            else:
                violations.append(
                    f"{name}: pyproject.toml present but no [project].version found"
                )

        source_files = sorted(plugin_dir.glob("src/*/__init__.py"))
        source_files.extend(sorted(plugin_dir.glob("src/*/_build_info.py")))
        for source_file in source_files:
            fallback_versions, read_error = _source_fallback_versions(source_file)
            label = source_file.relative_to(plugin_dir).as_posix()
            if read_error:
                violations.append(f"{name}: {read_error}: {label}")
                continue
            for variable, fallback_ver in fallback_versions.items():
                sources[f"{label}:{variable}"] = fallback_ver

        if name in mkt_versions:
            if mkt_versions[name]:
                sources["marketplace.json"] = mkt_versions[name]
        else:
            violations.append(f"{name}: missing from .github/plugin/marketplace.json")

        distinct = set(sources.values())
        if len(distinct) > 1:
            detail = ", ".join(f"{f}={v}" for f, v in sorted(sources.items()))
            violations.append(f"{name}: version mismatch ({detail})")

    if violations:
        print("Version-consistency violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nEvery plugin's version must agree across plugin.json, "
            "pyproject.toml (runtime plugins), marketplace.json entry, and any "
            "checked-in numeric development-version fallback. "
            "See CONTRIBUTING.md § 'Where the version lives'.",
            file=sys.stderr,
        )
        return 1

    print("check-version-consistency: all plugin version surfaces agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
