"""Materialize installer-engine canonical references into shipped payloads."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import installer_engine_ref as ier


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def materialize_ref_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path,
    dest_root: Path | None = None,
) -> list[str]:
    log: list[str] = []
    dest_root_r = (dest_root or dest_consumer_dir).resolve()
    bad_ancestor = _find_symlinked_ancestor(dest_consumer_dir, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing"]

    for ext in ("ps1", "sh"):
        source_script = source_consumer_dir / "scripts" / f"install.{ext}"
        dest_script = dest_consumer_dir / "scripts" / f"install.{ext}"
        match_count = ier.source_match_count(source_script, ext)
        is_registered_adopter = source_consumer_dir.name in ier.ADOPTERS
        if is_registered_adopter and match_count == 0:
            log.append(
                f"SKIP {dest_script}: registered adopter does not source installer-engine "
                "in this language variant"
            )
            continue
        if match_count > 1:
            log.append(
                f"SKIP {dest_script}: found {match_count} installer-engine source "
                "lines; expected exactly one"
            )
            continue
        ref = ier.find_engine_ref(source_script, ext)
        if ref is None:
            if is_registered_adopter:
                log.append(
                    f"SKIP {dest_script}: installer-engine source line is malformed or "
                    "unrecognized"
                )
            continue
        if not ier.ref_escapes_plugin_root(ref, source_consumer_dir):
            if is_registered_adopter and not ier.is_local_ref(ref, source_consumer_dir):
                log.append(
                    f"SKIP {dest_script}: installer-engine reference {ref.raw_path} "
                    f"(resolved {ref.resolved()}) is neither the exact local form "
                    f"{ier.local_line(ext)!r} nor the canonical reference form"
                )
            if ier.is_local_ref(ref, source_consumer_dir):
                continue
            continue
        if canonical_root.is_symlink():
            log.append(f"SKIP {dest_consumer_dir}: {canonical_root} is a symlink -- refusing")
            continue
        canonical_root_r = canonical_root.resolve()
        canonical_unresolved = canonical_root_r / "libs" / "installer-engine" / ref.file_name
        bad_ancestor = _find_symlinked_ancestor(canonical_unresolved, canonical_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_script}: {bad_ancestor} is a symlink -- refusing")
            continue
        canonical = ier.canonical_file(ext, repo_root=canonical_root)
        if not canonical.is_file():
            log.append(
                f"SKIP {dest_consumer_dir}: canonical source missing: "
                f"{canonical.relative_to(canonical_root).as_posix()}"
            )
            continue
        if ref.resolved() != canonical.resolve():
            log.append(
                f"SKIP {dest_script}: installer-engine reference {ref.raw_path} "
                f"(resolved {ref.resolved()}) is not "
                f"{canonical.relative_to(canonical_root).as_posix()}"
            )
            continue
        if canonical.is_symlink():
            log.append(f"SKIP {dest_script}: {canonical} is a symlink -- refusing")
            continue
        if dest_script.is_symlink():
            log.append(f"SKIP {dest_script}: is a symlink -- refusing to rewrite it blindly")
            continue
        dest_engine = dest_consumer_dir / "scripts" / ref.file_name
        bad_ancestor = _find_symlinked_ancestor(dest_engine, dest_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_engine}: {bad_ancestor} is a symlink -- refusing")
            continue
        if dest_engine.exists() or dest_engine.is_symlink():
            log.append(f"SKIP {dest_engine}: already exists -- refusing to overwrite")
            continue

        dest_engine.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical, dest_engine)
        if os.name != "nt":
            shutil.copymode(canonical, dest_engine)
        if not ier.rewrite_to_local(dest_script, ext):
            try:
                dest_engine.unlink()
            except OSError:
                pass
            log.append(f"SKIP {dest_script}: could not find installer-engine entry to rewrite")
            continue
        log.append(f"OK   {dest_engine} <- {canonical.relative_to(canonical_root).as_posix()}")
    return log


def materialize_refs(dest: Path, *, canonical_root: Path) -> list[str]:
    log: list[str] = []
    dest_plugins = dest / "plugins"
    if not dest_plugins.is_dir():
        return log
    for plugin in ier.ADOPTERS:
        dest_consumer_dir = dest_plugins / plugin
        if not dest_consumer_dir.is_dir():
            continue
        log.extend(materialize_ref_into(
            source_consumer_dir=canonical_root / "plugins" / plugin,
            dest_consumer_dir=dest_consumer_dir,
            canonical_root=canonical_root,
            dest_root=dest,
        ))
    return log
