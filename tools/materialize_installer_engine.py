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
    human_sources: frozenset[Path] | None = None,
) -> list[str]:
    log: list[str] = []
    dest_root_r = (dest_root or dest_consumer_dir).resolve()
    bad_ancestor = _find_symlinked_ancestor(dest_consumer_dir, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing"]
    if human_sources is None:
        human_sources = validate_human_output_sources(
            canonical_root, consumers=(source_consumer_dir,),
        )

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
        support_assets = []
        for name in ier.support_files(canonical.parent):
            source_asset = canonical.parent / name
            dest_asset = dest_engine.parent / name
            if (_find_symlinked_ancestor(source_asset, canonical_root_r) is not None
                    or not source_asset.is_file()
                    or _find_symlinked_ancestor(dest_asset, dest_root_r) is not None
                    or (dest_asset.exists() and dest_asset.read_bytes() != source_asset.read_bytes())):
                log.append(f"SKIP {dest_script}: required installer support asset unavailable or conflicting: {name}")
                break
            support_assets.append((source_asset, dest_asset))
        else:
            dest_engine.parent.mkdir(parents=True, exist_ok=True)
            for source_asset, dest_asset in support_assets:
                if not dest_asset.exists():
                    shutil.copyfile(source_asset, dest_asset)
            support_assets = None
        if support_assets is not None:
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
    if source_consumer_dir.resolve() in human_sources:
        log.extend(_materialize_human_output_into(
            dest_consumer_dir=dest_consumer_dir,
            canonical_root=canonical_root, dest_root=dest_root_r,
        ))
    return log


def validate_human_output_sources(
    canonical_root: Path, *, consumers=None,
) -> frozenset[Path]:
    """Validate original declarations before any owner mutates a same-root tree."""
    if consumers is None:
        consumers = (
            canonical_root / "plugins" / name
            for name in ier.human_output_adopters(canonical_root, ier.ADOPTERS)
        )
    declared = set()
    for consumer in consumers:
        script = consumer / "scripts" / "install.ps1"
        declaration = script.with_suffix(".human-output.json")
        if not declaration.exists() and not declaration.is_symlink():
            continue
        helper = script.parent / ier.HUMAN_HELPER
        canonical_helper = canonical_root / "libs" / "installer-engine" / ier.HUMAN_HELPER
        for path in (declaration, script, helper, canonical_helper):
            if _find_symlinked_ancestor(path, canonical_root) is not None:
                raise ValueError(f"{path}: linked human-output source refused")
        if (declaration.read_text(encoding="utf-8") != ier.human_output_json(script)
                or helper.read_bytes() != canonical_helper.read_bytes()):
            raise ValueError(f"{script}: conflicting native human-output assets")
        declared.add(consumer.resolve())
    return frozenset(declared)


def _materialize_human_output_into(
    *, dest_consumer_dir: Path, canonical_root: Path, dest_root: Path,
) -> list[str]:
    script = dest_consumer_dir / "scripts" / "install.ps1"
    declaration = script.with_suffix(".human-output.json")
    helper = script.parent / ier.HUMAN_HELPER
    canonical_helper = canonical_root / "libs" / "installer-engine" / ier.HUMAN_HELPER
    for path in (script, declaration, helper):
        if _find_symlinked_ancestor(path, dest_root) is not None:
            raise ValueError(f"{path}: linked human-output asset refused")
    if helper.exists() and helper.read_bytes() != canonical_helper.read_bytes():
        raise ValueError(f"{script}: conflicting native human-output assets")
    if not helper.exists():
        shutil.copyfile(canonical_helper, helper)
    expected = ier.human_output_json(script)
    if not declaration.is_file() or declaration.read_text(encoding="utf-8") != expected:
        declaration.write_text(expected, encoding="utf-8")
        return [f"OK   {declaration} <- materialized native output contract"]
    return []


def materialize_human_output_declarations(
    dest: Path, *, canonical_root: Path, human_sources: frozenset[Path],
) -> list[str]:
    """Rebind prevalidated producers after all owners have finished rewriting."""
    log = []
    for plugin in ier.human_output_adopters(canonical_root, ier.ADOPTERS):
        destination = dest / "plugins" / plugin
        if (destination.is_dir()
                and (canonical_root / "plugins" / plugin).resolve() in human_sources):
            log.extend(_materialize_human_output_into(
                dest_consumer_dir=destination, canonical_root=canonical_root,
                dest_root=dest,
            ))
    return log


def materialize_refs(
    dest: Path, *, canonical_root: Path, human_sources: frozenset[Path] | None = None,
) -> list[str]:
    log: list[str] = []
    dest_plugins = dest / "plugins"
    if not dest_plugins.is_dir():
        return log
    if human_sources is None:
        human_sources = validate_human_output_sources(canonical_root)
    for plugin in ier.ADOPTERS:
        dest_consumer_dir = dest_plugins / plugin
        if not dest_consumer_dir.is_dir():
            continue
        log.extend(materialize_ref_into(
            source_consumer_dir=canonical_root / "plugins" / plugin,
            dest_consumer_dir=dest_consumer_dir,
            canonical_root=canonical_root,
            dest_root=dest,
            human_sources=human_sources,
        ))
    return log
