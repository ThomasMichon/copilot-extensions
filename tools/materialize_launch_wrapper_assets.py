"""Materialize agent-worktrees' packaged launch-wrapper assets."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import launch_wrapper_assets_ref as lwar
import uv_editable_ref as uer


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def _resolve_within(root: Path, source_rel: str) -> Path | None:
    candidate = Path(source_rel)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved_candidate = candidate.resolve()
        resolved_root = root.resolve()
    except OSError:
        return None
    if uer.escapes_root(resolved_candidate, resolved_root):
        return None
    return resolved_candidate


def materialize_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path,
    dest_root: Path | None = None,
) -> list[str]:
    try:
        manifest = lwar.load_manifest(source_consumer_dir)
    except ValueError as exc:
        return [f"SKIP {source_consumer_dir}: {exc}"]
    if manifest is None:
        return []

    log: list[str] = []
    dest_root_r = (dest_root or dest_consumer_dir).resolve()
    bad_ancestor = _find_symlinked_ancestor(dest_consumer_dir, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing"]

    if canonical_root.is_symlink():
        return [f"SKIP {dest_consumer_dir}: {canonical_root} is a symlink -- refusing"]
    canonical_root_r = canonical_root.resolve()
    unresolved_source_dir = canonical_root / manifest.canonical_dir_raw
    bad_ancestor = _find_symlinked_ancestor(unresolved_source_dir, canonical_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {manifest.manifest_path}: {bad_ancestor} is a symlink -- refusing"]

    source_dir = _resolve_within(canonical_root, manifest.canonical_dir_raw)
    if source_dir is None:
        return [
            f"SKIP {manifest.manifest_path}: canonicalDir {manifest.canonical_dir_raw!r} "
            "escapes the canonical root -- refusing"
        ]
    if not source_dir.is_dir():
        return [
            f"SKIP {manifest.manifest_path}: canonical source missing: "
            f"{manifest.canonical_dir_raw}"
        ]

    dest_bin = manifest.payload_dir(plugin_dir=dest_consumer_dir)
    if dest_bin.is_symlink():
        return [f"SKIP {dest_bin}: is a symlink -- refusing"]
    bad_ancestor = _find_symlinked_ancestor(dest_bin.parent, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_bin}: {bad_ancestor} is a symlink -- refusing"]

    for name in manifest.files:
        source = source_dir / name
        if not source.is_file():
            return [
                f"SKIP {dest_bin / name}: canonical source missing: "
                f"{manifest.canonical_dir_raw}/{name}"
            ]
        if source.is_symlink():
            return [f"SKIP {source}: is a symlink -- refusing"]
        dest_file = dest_bin / name
        if dest_file.exists() or dest_file.is_symlink():
            return [f"SKIP {dest_file}: already exists -- refusing to overwrite"]

    dest_bin.mkdir(parents=True, exist_ok=True)
    for name in manifest.files:
        source = source_dir / name
        dest_file = dest_bin / name
        shutil.copyfile(source, dest_file)
        if os.name != "nt" and name.endswith(".sh"):
            shutil.copymode(source, dest_file)
        log.append(
            f"OK   {dest_file} <- "
            f"{Path(manifest.canonical_dir_raw) / name}"
        )
    return log


def materialize_all(dest: Path, *, canonical_root: Path) -> list[str]:
    log: list[str] = []
    dest_plugins = dest / "plugins"
    source_plugins = canonical_root / "plugins"
    if not dest_plugins.is_dir() or not source_plugins.is_dir():
        return log
    for manifest_path in sorted(source_plugins.glob(f"*/{lwar.MANIFEST_NAME}")):
        plugin = manifest_path.parent.name
        dest_consumer_dir = dest_plugins / plugin
        if not dest_consumer_dir.is_dir():
            continue
        log.extend(materialize_into(
            source_consumer_dir=manifest_path.parent,
            dest_consumer_dir=dest_consumer_dir,
            canonical_root=canonical_root,
            dest_root=dest,
        ))
    return log
