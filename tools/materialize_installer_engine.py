"""Materialize installer-engine canonical references into shipped payloads."""
from __future__ import annotations

import os
import json
import shutil
import stat
from pathlib import Path

import installer_engine_ref as ier


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    root_r = root.resolve()
    if path.is_absolute() and path.is_relative_to(root_r) and ".." not in path.parts:
        current = path
        while True:
            try:
                info = current.lstat()
            except FileNotFoundError:
                pass
            except OSError:
                break
            else:
                if stat.S_ISLNK(info.st_mode):
                    return current
                if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    break
            if current == root_r:
                return None
            current = current.parent
    # Aliases and reparse points retain the resolved walk rather than cached identity.
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def derived_file_pairs(
    canonical_root: Path, dest: Path, *, copied_consumer: tuple[Path, Path] | None = None,
) -> list[tuple[Path, Path]]:
    """Declared non-kit assets only; an absent consumer is never created."""
    if copied_consumer is not None:
        try:
            relative = copied_consumer[0].relative_to(canonical_root).as_posix()
        except ValueError:
            return []
        declared = {
            *(f"plugins/{name}" for name in (*ier.ADOPTERS, *ier.PYTHON_ADOPTERS)),
            *ier.STANDALONE_PYTHON_ADOPTERS, *ier.STANDALONE_NATIVE_ADOPTERS,
        }
        if relative not in declared:
            return []
    canonical = canonical_root / "libs" / "installer-engine"
    consumers: dict[Path, set[str]] = {}
    support = ier.support_files(canonical)
    for plugin in ier.ADOPTERS:
        consumer = canonical_root / "plugins" / plugin
        if copied_consumer is not None and consumer != copied_consumer[0]:
            continue
        names = consumers.setdefault(consumer, set())
        names.update(support)
        for ext in ("ps1", "sh"):
            script = consumer / "scripts" / f"install.{ext}"
            if script.exists() or script.is_symlink():
                if problem := derived_path_problem(script, canonical_root, required=True):
                    raise ValueError(problem)
                ref = ier.find_engine_ref(script, ext)
                if ref is not None and ier.is_local_ref(ref, consumer):
                    names.add(ref.file_name)
    for consumer in ier.python_adopter_dirs(canonical_root):
        consumers.setdefault(consumer, set()).update((*ier.PYTHON_FILES, *ier.SUPPORT_FILES))
    for consumer in ier.native_adopter_dirs(canonical_root):
        consumers.setdefault(consumer, set()).update(ier.STANDALONE_NATIVE_FILES)
    for plugin in ier.human_output_adopters(canonical_root, ier.ADOPTERS):
        consumer = canonical_root / "plugins" / plugin
        declaration = ier.human_output_entrypoint(consumer).with_suffix(".human-output.json")
        if declaration.exists() or declaration.is_symlink():
            consumers.setdefault(consumer, set()).add(ier.HUMAN_HELPER)
    pairs = []
    for consumer, names in sorted(consumers.items()):
        if copied_consumer is not None:
            if consumer != copied_consumer[0]:
                continue
            destination = copied_consumer[1]
        else:
            destination = dest / consumer.relative_to(canonical_root)
        if consumer.is_dir() and destination.is_dir():
            pairs.extend((canonical / name, destination / "scripts" / name) for name in sorted(names))
    return pairs


def derived_path_problem(path: Path, root: Path, *, required: bool) -> str | None:
    """A derived slot is ordinary and contained, never a link or shared inode."""
    if not path.absolute().is_relative_to(root.absolute()) or ".." in path.parts:
        return f"{path}: derived asset escapes its owner"
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if current == path and required:
                return f"{path}: required canonical asset is missing"
        else:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return f"{current}: linked derived asset refused"
            if current == path:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return f"{path}: derived asset must be an ordinary single-link file"
            elif not stat.S_ISDIR(info.st_mode):
                return f"{current}: derived parent is not an ordinary directory"
        if current == root:
            break
    return None


def _derived_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_mode, info.st_nlink,
    )


def _read_canonical_snapshot(path: Path, root: Path) -> tuple[bytes, tuple[int, ...], int]:
    """Read an already-admitted source, retaining identity across its byte read."""
    before = path.lstat()
    content = path.read_bytes()
    after = path.lstat()
    if _derived_identity(before) != _derived_identity(after):
        raise ValueError(f"{path}: canonical identity changed while reading")
    if problem := derived_path_problem(path, root, required=True):
        raise ValueError(problem)
    return content, _derived_identity(after), stat.S_IMODE(after.st_mode)


def _verify_derived_write(target: Path, root: Path, content: bytes) -> None:
    if problem := derived_path_problem(target, root, required=True):
        raise ValueError(problem)
    identity = target.lstat()
    observed = target.read_bytes()
    after = target.lstat()
    if observed != content:
        raise ValueError(f"{target}: postwrite bytes differ from admitted canonical content")
    if _derived_identity(identity) != _derived_identity(after):
        raise ValueError(f"{target}: derived identity changed during postwrite verification")
    if problem := derived_path_problem(target, root, required=True):
        raise ValueError(problem)


def materialize_derived_files(
    dest: Path, *, canonical_root: Path, copied_consumer: tuple[Path, Path] | None = None,
    file_names: frozenset[str] | None = None,
) -> list[str]:
    """Refresh approved derived slots in the candidate, never the source tree."""
    plan = []
    canonical: dict[Path, tuple[bytes, tuple[int, ...], int]] = {}
    try:
        for source, target in derived_file_pairs(canonical_root, dest, copied_consumer=copied_consumer):
            if file_names is not None and source.name not in file_names:
                continue
            previous = (
                canonical_root / target.relative_to(dest) if copied_consumer is None
                else copied_consumer[0] / target.relative_to(copied_consumer[1])
            )
            if source not in canonical:
                if problem := derived_path_problem(source, canonical_root, required=True):
                    return [f"SKIP {problem}"]
            for path, root, required in (
                (previous, canonical_root, False), (target, dest, False),
            ):
                if problem := derived_path_problem(path, root, required=required):
                    return [f"SKIP {problem}"]
            if source not in canonical:
                canonical[source] = _read_canonical_snapshot(source, canonical_root)
            content, _identity, mode = canonical[source]
            before = target.read_bytes() if target.exists() else None
            expected = previous.read_bytes() if previous.exists() else None
            if before is not None and before not in (content, expected):
                return [f"SKIP {target}: foreign content differs from its declared source preimage"]
            plan.append((source, target, content, before, mode))
        # All slots are admitted before the first candidate write.
        for source, snapshot in canonical.items():
            if problem := derived_path_problem(source, canonical_root, required=True):
                return [f"SKIP {problem}"]
            if _read_canonical_snapshot(source, canonical_root) != snapshot:
                return [f"SKIP {source}: canonical source changed before composition writes"]
        for source, target, content, before, mode in plan:
            if problem := derived_path_problem(target, dest, required=False):
                return [f"SKIP {problem}"]
            if (target.read_bytes() if target.exists() else None) != before:
                return [f"SKIP {target}: derived source or candidate changed during composition"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if os.name != "nt":
                target.chmod(mode)
        for source, target, content, _before, _mode in plan:
            _verify_derived_write(target, dest, content)
        for source, snapshot in canonical.items():
            if problem := derived_path_problem(source, canonical_root, required=True):
                return [f"SKIP {problem}"]
            if _read_canonical_snapshot(source, canonical_root) != snapshot:
                return [f"SKIP {source}: canonical source changed during composition"]
    except (OSError, ValueError) as error:
        return [f"SKIP installer derived assets: {error}"]
    return [f"OK   {target} <- {source.relative_to(canonical_root)}" for source, target, *_ in plan]


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
    if source_consumer_dir.resolve() in human_sources:
        helper = dest_consumer_dir / "scripts" / ier.HUMAN_HELPER
        canonical_helper = canonical_root / "libs" / "installer-engine" / ier.HUMAN_HELPER
        if not helper.exists() or helper.read_bytes() != canonical_helper.read_bytes():
            log.extend(_materialize_human_output_into(
                source_consumer_dir=source_consumer_dir,
                dest_consumer_dir=dest_consumer_dir,
                entrypoint=ier.human_output_entrypoint(source_consumer_dir).name,
                canonical_root=canonical_root, dest_root=dest_root_r,
            ))

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
        support_names = ier.support_files(canonical.parent)
        if any(
            not (canonical.parent / name).is_file()
            or not (dest_engine.parent / name).is_file()
            or (dest_engine.parent / name).read_bytes() != (canonical.parent / name).read_bytes()
            for name in support_names
        ):
            refreshed = materialize_derived_files(
                dest_consumer_dir, canonical_root=canonical_root,
                copied_consumer=(source_consumer_dir, dest_consumer_dir),
                file_names=frozenset(support_names),
            )
            log.extend(refreshed)
            if any(line.startswith("SKIP") for line in refreshed):
                return log
        support_assets = []
        for name in support_names:
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

        try:
            for path, root, required in (
                (canonical, canonical_root_r, True), (dest_engine, dest_root_r, False),
            ):
                if problem := derived_path_problem(path, root, required=required):
                    raise ValueError(problem)
            snapshot = _read_canonical_snapshot(canonical, canonical_root_r)
            dest_engine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical, dest_engine)
            if os.name != "nt":
                shutil.copymode(canonical, dest_engine)
            _verify_derived_write(dest_engine, dest_root_r, snapshot[0])
            if _read_canonical_snapshot(canonical, canonical_root_r) != snapshot:
                raise ValueError(f"{canonical}: canonical source changed during composition")
        except (OSError, ValueError) as error:
            log.append(f"SKIP installer native asset: {error}")
            return log
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
            source_consumer_dir=source_consumer_dir,
            dest_consumer_dir=dest_consumer_dir,
            entrypoint=ier.human_output_entrypoint(source_consumer_dir).name,
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
        script = ier.human_output_entrypoint(consumer)
        declaration = script.with_suffix(".human-output.json")
        if not declaration.exists() and not declaration.is_symlink():
            continue
        helper = script.parent / ier.HUMAN_HELPER
        canonical_helper = canonical_root / "libs" / "installer-engine" / ier.HUMAN_HELPER
        for path in (declaration, script, helper, canonical_helper):
            if _find_symlinked_ancestor(path, canonical_root) is not None:
                raise ValueError(f"{path}: linked human-output source refused")
            root = canonical_root if path == canonical_helper else consumer
            if problem := derived_path_problem(path, root, required=path != helper):
                raise ValueError(problem)
        try:
            original = declaration.read_text(encoding="utf-8")
            recorded = json.loads(original)
            if not isinstance(recorded, dict):
                raise ValueError("human-output declaration must be an object")
            initializer_sha256 = None
            if not helper.exists():
                initializer_sha256 = recorded.get("initializer_sha256")
                if initializer_sha256 is None:
                    raise ValueError("human-output initializer digest is missing")
            if original != ier.human_output_json(script, initializer_sha256=initializer_sha256):
                raise ValueError("human-output declaration differs from its source preimage")
        except (OSError, ValueError) as error:
            raise ValueError(f"{script}: conflicting native human-output assets") from error
        declared.add(consumer.resolve())
    return frozenset(declared)


def _materialize_human_output_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, entrypoint: str,
    canonical_root: Path, dest_root: Path,
) -> list[str]:
    script = dest_consumer_dir / "scripts" / entrypoint
    declaration = script.with_suffix(".human-output.json")
    helper = script.parent / ier.HUMAN_HELPER
    canonical_helper = canonical_root / "libs" / "installer-engine" / ier.HUMAN_HELPER
    for path in (script, declaration, helper):
        if problem := derived_path_problem(path, dest_root, required=path == script):
            raise ValueError(problem)
    log = []
    if not helper.exists() or helper.read_bytes() != canonical_helper.read_bytes():
        log = materialize_derived_files(
            dest_root, canonical_root=canonical_root,
            copied_consumer=(source_consumer_dir, dest_consumer_dir),
            file_names=frozenset({ier.HUMAN_HELPER}),
        )
        if any(line.startswith("SKIP") for line in log) or (
            not helper.is_file() or helper.read_bytes() != canonical_helper.read_bytes()
        ):
            raise ValueError(f"{script}: conflicting native human-output assets")
    expected = ier.human_output_json(script)
    if not declaration.is_file() or declaration.read_bytes().decode("utf-8") != expected:
        declaration.write_bytes(expected.encode("utf-8"))
        log.append(f"OK   {declaration} <- materialized native output contract")
    return log


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
                source_consumer_dir=canonical_root / "plugins" / plugin,
                dest_consumer_dir=destination,
                entrypoint=ier.human_output_entrypoint(
                    canonical_root / "plugins" / plugin,
                ).name,
                canonical_root=canonical_root,
                dest_root=dest,
            ))
    return log


def materialize_refs(
    dest: Path, *, canonical_root: Path, human_sources: frozenset[Path] | None = None,
) -> list[str]:
    if human_sources is None:
        human_sources = validate_human_output_sources(canonical_root)
    log = materialize_derived_files(dest, canonical_root=canonical_root)
    if any(line.startswith("SKIP") for line in log):
        return log
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
            human_sources=human_sources,
        ))
    return log
