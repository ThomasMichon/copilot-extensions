#!/usr/bin/env python3
"""Guard vendored libs shared across plugins against silent drift (dotfiles #929).

Several shared libraries (``ssh-manager``, ``credential-relay``, ``zdd``,
``config-migrate``, ``plugin-resolve``) are **vendored per plugin** -- each
plugin carries its own copy under ``plugins/<plugin>/libs/<lib>`` because a
plugin installed standalone from the marketplace can only reference libs inside
its own directory (``[tool.uv.sources] <lib> = { path = "libs/<lib>" }``).

The hazard: every copy publishes the SAME distribution name and version
(``agent-ssh-manager==0.1.0-devN``). When agent-bridge's installer also installs
a sibling plugin (agent-codespaces) into the same venv, whichever copy is
installed last wins -- and if the copies' **source** has drifted, the daemon
silently runs the wrong code. This actually happened: a change added to
agent-bridge's ssh-manager (``build_remote_exec_args``) was not synced to
agent-codespaces's copy, so a redeploy that reinstalled the sibling downgraded
``ssh_manager`` and crashed the daemon on every CodeSpace dispatch with
``ImportError: cannot import name 'build_remote_exec_args'`` (dotfiles #929).

This check freezes the only invariant that keeps that safe:

* every lib vendored in >=2 plugins must have a **byte-identical ``src/`` tree**
  across all its copies (the importable surface -- the thing that actually gets
  installed and imported), and
* all copies must declare the **same version** (same name + same version + same
  source => pip/uv can dedupe them and no "last writer wins on identical
  version" skew is possible).

Deliberately NOT compared: ``pyproject.toml`` build-dependency pins and
``README`` -- Dependabot bumps a single copy's build deps at a time and that is
harmless as long as the source and version agree. (The version line *is*
checked, separately, from each pyproject.)

Usage::

    python tools/check-vendored-libs-sync.py          # verify (CI / pre-push)
    python tools/check-vendored-libs-sync.py --list     # show the vendored-lib map
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

# Subtrees under a lib copy that are build/artifact noise, never source of truth.
_IGNORE_PARTS = {"build", ".venv", "__pycache__", "dist"}
_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE)


def _lib_copies() -> dict[str, list[Path]]:
    """Map ``lib name -> [copy paths]`` for every ``plugins/*/libs/*`` dir."""
    copies: dict[str, list[Path]] = {}
    if not PLUGINS_DIR.is_dir():
        return copies
    for plugin in sorted(PLUGINS_DIR.iterdir()):
        libs = plugin / "libs"
        if not libs.is_dir():
            continue
        for lib in sorted(libs.iterdir()):
            if lib.is_dir():
                copies.setdefault(lib.name, []).append(lib)
    return copies


def _src_files(lib_dir: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every file under ``<lib>/src`` (artifacts skipped)."""
    src = lib_dir / "src"
    out: dict[str, str] = {}
    if not src.is_dir():
        return out
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        if _IGNORE_PARTS & set(f.relative_to(src).parts):
            continue
        if f.suffix in (".pyc", ".pyo") or ".egg-info" in str(f):
            continue
        rel = f.relative_to(src).as_posix()
        out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _declared_version(lib_dir: Path) -> str | None:
    pp = lib_dir / "pyproject.toml"
    if not pp.exists():
        return None
    m = _VERSION_RE.search(pp.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def verify() -> list[str]:
    """Return human-readable problems; empty means the check passes."""
    problems: list[str] = []
    base = PLUGINS_DIR.parent  # REPO in production; the tmp root under test
    for lib, paths in _lib_copies().items():
        if len(paths) < 2:
            continue
        rel_names = [str(p.relative_to(base)) for p in paths]

        # 1) src/ trees must be byte-identical across all copies.
        maps = [_src_files(p) for p in paths]
        ref_map, ref_name = maps[0], rel_names[0]
        for other_map, other_name in zip(maps[1:], rel_names[1:], strict=True):
            all_rel = set(ref_map) | set(other_map)
            for rel in sorted(all_rel):
                a, b = ref_map.get(rel), other_map.get(rel)
                if a is None:
                    problems.append(
                        f"{lib}: src/{rel} missing in {ref_name} "
                        f"(present in {other_name})"
                    )
                elif b is None:
                    problems.append(
                        f"{lib}: src/{rel} missing in {other_name} "
                        f"(present in {ref_name})"
                    )
                elif a != b:
                    problems.append(
                        f"{lib}: src/{rel} DIFFERS between {ref_name} and {other_name} "
                        "-- re-sync the vendored copies"
                    )

        # 2) declared versions must all match.
        versions = {rel_names[i]: _declared_version(paths[i]) for i in range(len(paths))}
        distinct = {v for v in versions.values() if v is not None}
        if len(distinct) > 1:
            detail = ", ".join(f"{n}={v}" for n, v in versions.items())
            problems.append(
                f"{lib}: version skew across copies ({detail}) "
                "-- bump all copies to the same version"
            )
        missing_ver = [n for n, v in versions.items() if v is None]
        if missing_ver:
            problems.append(f"{lib}: no version declared in {', '.join(missing_ver)}")
    return problems


def _print_list() -> None:
    base = PLUGINS_DIR.parent
    for lib, paths in sorted(_lib_copies().items()):
        if len(paths) < 2:
            continue
        ver = _declared_version(paths[0]) or "?"
        print(f"{lib}  (v{ver}, {len(paths)} copies):")
        for p in paths:
            print(f"    {p.relative_to(base)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the vendored-lib map and exit")
    args = ap.parse_args()
    if args.list:
        _print_list()
        return 0
    problems = verify()
    if problems:
        print("check-vendored-libs-sync: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nRe-sync the drifted vendored lib copies so every plugin ships the "
            "same source (and version). A source change to one copy MUST be "
            "propagated to all, with a version bump -- otherwise a sibling-plugin "
            "install can silently downgrade the shared package (dotfiles #929).",
            file=sys.stderr,
        )
        return 1
    shared = {k: v for k, v in _lib_copies().items() if len(v) >= 2}
    print(f"check-vendored-libs-sync: OK ({len(shared)} shared libs in sync).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
