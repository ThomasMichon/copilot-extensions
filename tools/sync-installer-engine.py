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
Linked destinations are refused before reading, copying or changing permissions.
Explicit standalone native adopters receive their declared canonical helpers;
the Python-free foundation does not own another prerequisite downloader.

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
PYTHON_ADOPTERS = ier.PYTHON_ADOPTERS
BOOTSTRAP_ADOPTERS = ("agent-containers", "agent-codespaces")
BOOTSTRAP_FILE = "bootstrap-python.ps1"


def vendor_pairs() -> list[tuple[Path, Path]]:
    return [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "scripts" / name,
        )
        for plugin in ADOPTERS
        for name in (*FILES, *ier.support_files(CANONICAL_DIR))
    ]


def bootstrap_vendor_pairs() -> list[tuple[Path, Path]]:
    return [
        (CANONICAL_DIR / BOOTSTRAP_FILE, REPO / "plugins" / plugin / "scripts" / BOOTSTRAP_FILE)
        for plugin in BOOTSTRAP_ADOPTERS
        if os.path.lexists(REPO / "plugins" / plugin)
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
            f"(resolved {ref.resolved()}) which is not {canonical.relative_to(REPO).as_posix()}",
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
    for name in ier.support_files(CANONICAL_DIR):
        source = CANONICAL_DIR / name
        destination = plugin_dir / "scripts" / name
        relative = destination.relative_to(REPO).as_posix()
        if uer._find_symlinked_ancestor(source, REPO.resolve()) is not None or destination.is_symlink():
            problems.append(f"{relative}: support asset is linked -- refusing")
        elif not source.is_file():
            problems.append(f"canonical source missing: {source.relative_to(REPO)}")
        elif not destination.is_file() or destination.read_bytes() != source.read_bytes():
            problems.append(f"{relative} is missing or differs from {source.relative_to(REPO)}")
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
    for name in ier.support_files(CANONICAL_DIR):
        source = CANONICAL_DIR / name
        destination = plugin_dir / "scripts" / name
        if uer._find_symlinked_ancestor(source, REPO.resolve()) is not None or not source.is_file():
            problems.append(f"canonical support source missing or linked: {source.relative_to(REPO)}")
        elif destination.is_symlink() or (
            destination.exists() and destination.read_bytes() != source.read_bytes()
        ):
            problems.append(f"{destination.relative_to(REPO)} differs from canonical support source")
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
        and candidate not in ier.native_adopter_dirs(REPO)
        and (
            any((candidate / "scripts" / name).is_file() for name in FILES)
            or _has_any_ref(candidate.name)
        )
    )


def verify() -> list[str]:
    problems: list[str] = []
    try:
        projected = ier.native_executable_config(CANONICAL_DIR)
        config = CANONICAL_DIR / "installer_dependency_config.py"
        if projected is not None and config.read_text(encoding="utf-8") != projected:
            problems.append(f"{config}: canonical native executable projection drift")
    except (OSError, ValueError, SyntaxError) as error:
        problems.append(str(error))
    for plugin in unregistered_adopters():
        problems.append(
            f"plugins/{plugin} uses installer-engine but is not listed in "
            "ADOPTERS, so the tool does not track its expected dev-time form"
        )
    for plugin in ADOPTERS:
        problems.extend(_adopter_problems(plugin))
    if ier.human_output_enabled(CANONICAL_DIR):
        source = CANONICAL_DIR / ier.HUMAN_HELPER
        for plugin in ier.human_output_adopters(REPO, ADOPTERS):
            scripts = REPO / "plugins" / plugin / "scripts"
            helper = scripts / ier.HUMAN_HELPER
            script = ier.human_output_entrypoint(scripts.parent)
            declaration = script.with_suffix(".human-output.json")
            try:
                for path in (source, helper, script, declaration):
                    if uer._find_symlinked_ancestor(path, REPO.resolve()) is not None:
                        raise ValueError(f"{path}: linked human-output asset refused")
                if helper.read_bytes() != source.read_bytes():
                    raise ValueError(f"{helper}: native human-output initializer differs")
                if declaration.read_text(encoding="utf-8") != ier.human_output_json(script):
                    raise ValueError(f"{declaration}: native human-output declaration drift")
            except (OSError, ValueError) as error:
                problems.append(str(error))
    for consumer in ier.python_adopter_dirs(REPO, plugins=PYTHON_ADOPTERS):
        for name in (*ier.PYTHON_FILES, *ier.SUPPORT_FILES):
            source = CANONICAL_DIR / name
            destination = consumer / "scripts" / name
            indirect = next(
                (found for path in (source, destination)
                 if (found := uer._find_symlinked_ancestor(path, REPO.resolve())) is not None),
                None,
            )
            if indirect is not None:
                problems.append(f"{indirect} is a symlink -- refusing")
            elif not destination.is_file() or destination.read_bytes() != source.read_bytes():
                problems.append(f"{destination.relative_to(REPO)} differs from canonical Python engine")
    for consumer in ier.native_adopter_dirs(REPO):
        for name in ier.STANDALONE_NATIVE_FILES:
            source, destination = CANONICAL_DIR / name, consumer / "scripts" / name
            for path in (source, destination):
                indirect = uer._find_symlinked_ancestor(path, REPO.resolve())
                if indirect is not None:
                    problems.append(f"{indirect} is a symlink -- refusing")
                    break
            else:
                if not source.is_file() or not destination.is_file() or destination.read_bytes() != source.read_bytes():
                    problems.append(f"{destination.relative_to(REPO)} differs from canonical native engine")
                elif os.name != "nt" and stat.S_IMODE(destination.stat().st_mode) != stat.S_IMODE(source.stat().st_mode):
                    problems.append(f"{destination.relative_to(REPO)} has a different canonical native mode")
    for source, destination in bootstrap_vendor_pairs():
        indirect = next(
            (found for path in (source, destination)
             if (found := uer._find_symlinked_ancestor(path, REPO.resolve())) is not None),
            None,
        )
        if indirect is not None:
            problems.append(f"{indirect} is a symlink -- refusing")
        elif not source.is_file() or not destination.is_file() or source.read_bytes() != destination.read_bytes():
            problems.append(f"{destination.relative_to(REPO)} differs from canonical evaluator bootstrap")
    plugins = REPO / "plugins"
    for candidate in plugins.iterdir() if plugins.is_dir() else ():
        native_files = (
            ier.STANDALONE_NATIVE_FILES if candidate in ier.native_adopter_dirs(REPO) else ()
        )
        if candidate.name not in PYTHON_ADOPTERS and any(
            (candidate / "scripts" / name).exists()
            for name in ier.PYTHON_FILES if name not in native_files
        ):
            problems.append(f"{candidate.name} has an unregistered Python installer engine")
    return problems


def sync() -> list[str]:
    written: list[str] = []
    projected = ier.native_executable_config(CANONICAL_DIR)
    config = CANONICAL_DIR / "installer_dependency_config.py"
    if projected is not None and config.read_text(encoding="utf-8") != projected:
        config.write_text(projected, encoding="utf-8")
        written.append(config.relative_to(REPO).as_posix())
    for plugin in ADOPTERS:
        mode, _states, structural_problems = _wrapper_mode(plugin)
        if structural_problems:
            raise RuntimeError(
                "installer-engine adopter is not in a complete recognized form:\n  - "
                + "\n  - ".join(structural_problems)
            )
        for name in (*FILES, *ier.support_files(CANONICAL_DIR)):
            source = CANONICAL_DIR / name
            bad_ancestor = uer._find_symlinked_ancestor(source, REPO.resolve())
            if bad_ancestor is not None:
                raise RuntimeError(f"{bad_ancestor} is a symlink -- refusing")
            destination = REPO / "plugins" / plugin / "scripts" / name
            bad_destination = uer._find_symlinked_ancestor(destination, REPO.resolve())
            if bad_destination is not None:
                raise RuntimeError(f"{bad_destination} is a symlink -- refusing")
            if not source.is_file():
                raise FileNotFoundError(f"canonical source missing: {source}")
            if mode == "canonical" and name in FILES:
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                    written.append(f"removed {destination.relative_to(REPO).as_posix()}")
                continue
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
    if ier.human_output_enabled(CANONICAL_DIR):
        source = CANONICAL_DIR / ier.HUMAN_HELPER
        for plugin in ier.human_output_adopters(REPO, ADOPTERS):
            scripts = REPO / "plugins" / plugin / "scripts"
            helper = scripts / ier.HUMAN_HELPER
            script = ier.human_output_entrypoint(scripts.parent)
            declaration = script.with_suffix(".human-output.json")
            for path in (source, helper, script, declaration):
                linked = uer._find_symlinked_ancestor(path, REPO.resolve())
                if linked is not None:
                    raise RuntimeError(f"{linked}: linked human-output asset refused")
            if not helper.is_file() or helper.read_bytes() != source.read_bytes():
                shutil.copyfile(source, helper)
                written.append(helper.relative_to(REPO).as_posix())
            expected = ier.human_output_json(script)
            if not declaration.is_file() or declaration.read_text(encoding="utf-8") != expected:
                declaration.write_text(expected, encoding="utf-8")
                written.append(declaration.relative_to(REPO).as_posix())
    for consumer in ier.python_adopter_dirs(REPO, plugins=PYTHON_ADOPTERS):
        for name in (*ier.PYTHON_FILES, *ier.SUPPORT_FILES):
            source = CANONICAL_DIR / name
            destination = consumer / "scripts" / name
            for path in (source, destination):
                indirect = uer._find_symlinked_ancestor(path, REPO.resolve())
                if indirect is not None:
                    raise RuntimeError(f"{indirect} is a symlink -- refusing")
            if destination.is_file() and destination.read_bytes() == source.read_bytes():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            written.append(destination.relative_to(REPO).as_posix())
    for consumer in ier.native_adopter_dirs(REPO):
        for name in ier.STANDALONE_NATIVE_FILES:
            source, destination = CANONICAL_DIR / name, consumer / "scripts" / name
            for path in (source, destination):
                indirect = uer._find_symlinked_ancestor(path, REPO.resolve())
                if indirect is not None:
                    raise RuntimeError(f"{indirect} is a symlink -- refusing")
            same_content = destination.is_file() and destination.read_bytes() == source.read_bytes()
            same_mode = destination.is_file() and (
                os.name == "nt"
                or stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
            )
            if same_content and same_mode:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not same_content:
                shutil.copyfile(source, destination)
            if os.name != "nt":
                shutil.copymode(source, destination)
            written.append(destination.relative_to(REPO).as_posix())
    for source, destination in bootstrap_vendor_pairs():
        for path in (source, destination):
            indirect = uer._find_symlinked_ancestor(path, REPO.resolve())
            if indirect is not None:
                raise RuntimeError(f"{indirect}: linked evaluator bootstrap refused")
        if not source.is_file():
            raise FileNotFoundError(f"canonical evaluator bootstrap is missing: {source}")
        if destination.is_file() and destination.read_bytes() == source.read_bytes():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        written.append(destination.relative_to(REPO).as_posix())
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify vendored copies without changing files",
    )
    parser.add_argument(
        "--only-python-engine", action="store_true",
        help="synchronize only the canonical Python engine files, preserving concurrent native/support edits",
    )
    arguments = parser.parse_args()
    if arguments.only_python_engine:
        pairs = [
            (CANONICAL_DIR / name, consumer / "scripts" / name)
            for consumer in ier.python_adopter_dirs(REPO, plugins=PYTHON_ADOPTERS)
            for name in ier.PYTHON_FILES
        ]
        for source, destination in pairs:
            for path in (source, destination):
                if uer._find_symlinked_ancestor(path, REPO.resolve()) is not None:
                    raise RuntimeError("linked Python installer-engine source or destination")
            if not source.is_file():
                raise FileNotFoundError(f"canonical Python engine missing: {source.name}")
        drift = []
        for source, destination in pairs:
            if destination.is_file() and destination.read_bytes() == source.read_bytes():
                continue
            drift.append(destination.relative_to(REPO).as_posix())
            if not arguments.check:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        if arguments.check and drift:
            print("\n".join(drift), file=sys.stderr)
            return 1
        print(f"Python installer-engine scope: {len(pairs)} files, {len(drift)} differences.")
        return 0
    if arguments.check:
        problems = verify()
        if problems:
            print("installer-engine vendoring is out of sync:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("\nRun: python tools/sync-installer-engine.py", file=sys.stderr)
            return 1
        print(
            f"installer-engine files in sync across {len(ADOPTERS)} shell adopter(s) "
            f"and {len(ier.python_adopter_dirs(REPO, plugins=PYTHON_ADOPTERS))} Python consumer(s), "
            f"plus {len(ier.native_adopter_dirs(REPO))} standalone native consumer(s)."
        )
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
