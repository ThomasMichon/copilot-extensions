#!/usr/bin/env python3
"""Keep installer-engine adopters consistent with canonical.

The installer engine is a **vendored** source surface, not a runtime
cross-plugin dependency: every adopting plugin ships its own byte-identical
copy under ``scripts/installer-engine.*`` because marketplace plugins are
installed independently. The canonical sources live under
``libs/installer-engine/``.

Adopters can now exist in one of two valid dev-time forms:

* the older byte-vendored copy under ``scripts/installer-engine.*``; or
* the Phase-2 canonical-reference form, where ``install.sh``/``install.ps1``
  source ``libs/installer-engine/installer-engine.{sh,ps1}`` directly and no
  plugin-local copy exists on ``dev`` at all.

This tool's ``--check`` verifies both forms correctly. Its write mode keeps
vendored adopters byte-identical and removes stale local copies from
canonical-reference adopters.

Usage::

    python tools/sync-installer-engine.py          # copy canonical -> plugins
    python tools/sync-installer-engine.py --check  # verify in sync
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier
import uv_editable_ref as uer

REPO = ier.REPO
CANONICAL_DIR = ier.CANONICAL_DIR
FILES = ier.FILES
# Add future adopters here as later rollout phases land. The list stays explicit
# so the canonical engine can migrate one plugin at a time.
ADOPTERS = ier.ADOPTERS


def vendor_pairs() -> list[tuple[Path, Path]]:
    return [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "scripts" / name,
        )
        for plugin in ADOPTERS
        for name in FILES
    ]


def _plugin_dir(plugin: str) -> Path:
    return REPO / "plugins" / plugin


def _ref_state(plugin: str, ext: str) -> tuple[str, str | None]:
    """Return the install-script state for one language variant.

    States:
    - ``none``: no installer-engine source line
    - ``duplicate``: more than one source line
    - ``canonical``: exact canonical-reference form
    - ``local``: exact byte-vendored local-reference form
    - ``malformed``: exactly one source line, but neither exact valid form
    """
    plugin_dir = _plugin_dir(plugin)
    script_path = plugin_dir / "scripts" / f"install.{ext}"
    match_count = ier.source_match_count(script_path, ext)
    if match_count == 0:
        return ("none", f"plugins/{plugin}/scripts/install.{ext} does not source installer-engine")
    if match_count > 1:
        return (
            "duplicate",
            f"plugins/{plugin}/scripts/install.{ext} contains {match_count} "
            "installer-engine source lines; expected exactly one",
        )
    ref = ier.find_engine_ref(script_path, ext)
    if ref is None:
        return (
            "malformed",
            f"plugins/{plugin}/scripts/install.{ext} does not contain exactly one "
            "recognized installer-engine source line",
        )
    if ier.is_canonical_ref(ref, plugin_dir, repo_root=REPO):
        return ("canonical", None)
    if ier.is_local_ref(ref, plugin_dir):
        return ("local", None)
    if ier.ref_escapes_plugin_root(ref, plugin_dir):
        canonical = ier.canonical_file(ext, repo_root=REPO)
        return (
            "malformed",
            f"plugins/{plugin}/scripts/install.{ext} references {ref.raw_path} "
            f"(resolved {ref.resolved()}) which is not {canonical.relative_to(REPO)}",
        )
    return (
        "malformed",
        f"plugins/{plugin}/scripts/install.{ext} references {ref.raw_path} "
        f"(resolved {ref.resolved()}) which is neither {ier.local_line(ext)!r} "
        f"nor {ier.canonical_line(ext)!r}",
    )


def _wrapper_mode(plugin: str) -> tuple[str | None, dict[str, str], list[str]]:
    """Return a registered adopter's wrapper mode and any structural problems."""
    problems: list[str] = []
    states: dict[str, str] = {}
    for ext in ("ps1", "sh"):
        state, problem = _ref_state(plugin, ext)
        states[ext] = state
        if problem is not None:
            problems.append(problem)
    if problems:
        return None, states, problems

    if len(set(states.values())) != 1:
        problems.append(
            f"plugins/{plugin} mixes installer-engine forms across install.ps1/install.sh: "
            f"ps1={states['ps1']}, sh={states['sh']}"
        )
        return None, states, problems

    return states["ps1"], states, []


def _local_copy_problems(plugin: str) -> list[str]:
    problems: list[str] = []
    plugin_dir = _plugin_dir(plugin)
    for ext in ("ps1", "sh"):
        source = ier.canonical_file(ext, repo_root=REPO)
        bad_ancestor = uer._find_symlinked_ancestor(source, REPO.resolve())
        if bad_ancestor is not None:
            problems.append(f"{bad_ancestor} is a symlink -- refusing")
            continue
        destination = plugin_dir / "scripts" / f"installer-engine.{ext}"
        relative = destination.relative_to(REPO).as_posix()
        if not source.is_file():
            problems.append(f"canonical source missing: {source.relative_to(REPO)}")
        elif not destination.is_file():
            problems.append(f"{relative} is missing")
        elif destination.read_bytes() != source.read_bytes():
            problems.append(f"{relative} differs from {source.relative_to(REPO)}")
        elif os.name != "nt" and stat.S_IMODE(destination.stat().st_mode) != stat.S_IMODE(
            source.stat().st_mode
        ):
            problems.append(f"{relative} mode differs from {source.relative_to(REPO)}")
    return problems


def _canonical_copy_problems(plugin: str) -> list[str]:
    problems: list[str] = []
    plugin_dir = _plugin_dir(plugin)
    for ext in ("ps1", "sh"):
        canonical = ier.canonical_file(ext, repo_root=REPO)
        bad_ancestor = uer._find_symlinked_ancestor(canonical, REPO.resolve())
        if bad_ancestor is not None:
            problems.append(f"{bad_ancestor} is a symlink -- refusing")
            continue
        if not canonical.is_file():
            problems.append(f"canonical source missing: {canonical.relative_to(REPO)}")
        destination = plugin_dir / "scripts" / f"installer-engine.{ext}"
        if destination.exists() or destination.is_symlink():
            problems.append(
                f"{destination.relative_to(REPO).as_posix()} should not exist in dev once "
                f"{plugin} uses the canonical reference"
            )
    return problems


def _adopter_problems(plugin: str) -> list[str]:
    """Validate a registered adopter as exactly one complete form."""
    mode, states, problems = _wrapper_mode(plugin)
    if problems:
        if set(states.values()) == {"none"}:
            problems.extend(_local_copy_problems(plugin))
        return problems

    if mode == "canonical":
        return _canonical_copy_problems(plugin)
    if mode == "local":
        return _local_copy_problems(plugin)

    problems.append(f"plugins/{plugin} is in unexpected installer-engine state: {mode}")
    return problems


def _uses_canonical_ref(plugin: str) -> bool:
    mode, _states, problems = _wrapper_mode(plugin)
    return not problems and mode == "canonical"


def _has_escaping_ref(plugin: str) -> bool:
    plugin_dir = _plugin_dir(plugin)
    return any(
        ier.ref_escapes_plugin_root(ref, plugin_dir)
        for ref in ier.plugin_ref_map(plugin_dir).values()
    )


def _has_any_ref(plugin: str) -> bool:
    plugin_dir = _plugin_dir(plugin)
    return any(
        ier.source_match_count(plugin_dir / "scripts" / f"install.{ext}", ext) > 0
        for ext in ("ps1", "sh")
    )


def unregistered_adopters() -> list[str]:
    """Plugins that vendor an installer-engine file but are absent from ``ADOPTERS``.

    A plugin that ships ``scripts/installer-engine.ps1``/``.sh`` dot-sources it
    from its own installer, so a copy outside the adopter list never receives
    canonical updates while this tool still reports everything in sync. That
    drift is invisible until the stale copy misbehaves, so name it as a
    problem instead of staying silent.
    """
    plugins_root = REPO / "plugins"
    if not plugins_root.is_dir():
        return []
    return sorted(
        candidate.name
        for candidate in plugins_root.iterdir()
        if candidate.name not in ADOPTERS
        and (
            any((candidate / "scripts" / name).is_file() for name in FILES)
            or _has_any_ref(candidate.name)
        )
    )


def verify() -> list[str]:
    problems: list[str] = []
    for plugin in unregistered_adopters():
        problems.append(
            f"plugins/{plugin} uses installer-engine but is not listed in "
            "ADOPTERS, so the tool does not track its expected dev-time form"
        )
    for plugin in ADOPTERS:
        problems.extend(_adopter_problems(plugin))
    return problems


def sync() -> list[str]:
    written: list[str] = []
    for plugin in ADOPTERS:
        mode, _states, structural_problems = _wrapper_mode(plugin)
        if structural_problems:
            raise RuntimeError(
                "installer-engine adopter is not in a complete recognized form:\n  - "
                + "\n  - ".join(structural_problems)
            )
        if mode == "canonical":
            for name in FILES:
                source = CANONICAL_DIR / name
                bad_ancestor = uer._find_symlinked_ancestor(source, REPO.resolve())
                if bad_ancestor is not None:
                    raise RuntimeError(f"{bad_ancestor} is a symlink -- refusing")
                if not source.is_file():
                    raise FileNotFoundError(f"canonical source missing: {source}")
                destination = REPO / "plugins" / plugin / "scripts" / name
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                    written.append(f"removed {destination.relative_to(REPO).as_posix()}")
            continue
        for name in FILES:
            source = CANONICAL_DIR / name
            bad_ancestor = uer._find_symlinked_ancestor(source, REPO.resolve())
            if bad_ancestor is not None:
                raise RuntimeError(f"{bad_ancestor} is a symlink -- refusing")
            destination = REPO / "plugins" / plugin / "scripts" / name
            if not source.is_file():
                raise FileNotFoundError(f"canonical source missing: {source}")
            content_matches = destination.is_file() and destination.read_bytes() == source.read_bytes()
            mode_matches = destination.is_file() and (
                os.name == "nt"
                or stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
            )
            if content_matches and mode_matches:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not content_matches:
                shutil.copyfile(source, destination)
            if os.name != "nt":
                shutil.copymode(source, destination)
            written.append(destination.relative_to(REPO).as_posix())
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify vendored copies without changing files",
    )
    arguments = parser.parse_args()
    if arguments.check:
        problems = verify()
        if problems:
            print("installer-engine vendoring is out of sync:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("\nRun: python tools/sync-installer-engine.py", file=sys.stderr)
            return 1
        print(f"installer-engine files in sync across {len(ADOPTERS)} adopter(s).")
        return 0

    written = sync()
    if written:
        print(f"Synced installer-engine files ({len(written)} file(s)):")
        for path in written:
            print(f"  + {path}")
    else:
        print("Installer-engine vendoring already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
