"""Canonical source fingerprint framing and filesystem inventory, without runtime activation."""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import os
from os import PathLike
from pathlib import Path
import stat
from types import ModuleType


def normalize_windows_extended_path(target: str) -> str:
    """Normalize extended drive and UNC prefixes, preserving ordinary target names."""
    unc_prefix = "\\\\?\\UNC\\"
    if target[:len(unc_prefix)].casefold() == unc_prefix.casefold():
        return "\\\\" + target[len(unc_prefix):]
    if target.startswith("\\\\?\\"):
        return target[4:]
    return target



def native_path(path: Path) -> Path:
    if os.name != "nt":
        return Path(path)
    value = os.path.abspath(path)
    if not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)



def fingerprint_entries(
    entries: Mapping[Path, str], *, label: Callable[[Path], str],
    root_label: Callable[[Path], str], roots: Collection[Path],
    normalize_link: Callable[[str], str], path_type: Callable[[str], Path],
    file_hashes: MutableMapping[Path, str] | None, on_file: Callable[[Path, bytes], None] | None,
    on_progress: Callable[[], None] | None = None,
    io_path: Callable[[Path], Path] = lambda path: path,
    reported_path: Callable[[Path], Path] = lambda path: path,
    _os_module: ModuleType = os,
) -> str:
    """Hash validated entries with the unchanged ordered, length-framed format."""
    import hashlib
    os = _os_module

    def frame(data: bytes) -> bytes:
        return len(data).to_bytes(8, "big") + data

    ordered = sorted(entries, key=label)
    parallel = on_file is None and len(ordered) > 1
    digest = hashlib.sha256()
    with ThreadPoolExecutor(max_workers=4) if parallel else nullcontext() as readers:
        for start in range(0, len(ordered), 4):
            batch = ordered[start:start + 4]
            pending = {
                entry: readers.submit(io_path(entry).read_bytes)
                for entry in batch if entries[entry] != "symlink"
            } if readers is not None else {}
            for entry in batch:
                kind = entries[entry]
                if kind == "symlink":
                    try:
                        target = normalize_link(os.readlink(io_path(entry)))
                    except OSError as exc:
                        raise OSError(f"fingerprint_source: could not read symlink {entry}: {exc}") from exc
                    if os.path.isabs(target):
                        try:
                            resolved = path_type(normalize_link(str(
                                io_path(path_type(target)).resolve(),
                            )))
                        except OSError:
                            resolved = None
                        if resolved is not None and any(
                            resolved == root or resolved.is_relative_to(root) for root in roots
                        ):
                            target = root_label(resolved)
                    data = path_type(target).as_posix().encode("utf-8")
                else:
                    try:
                        data = (
                            pending[entry].result() if entry in pending
                            else io_path(entry).read_bytes()
                        )
                    except OSError as exc:
                        raise OSError(f"fingerprint_source: could not read {entry}: {exc}") from exc
                    if file_hashes is not None or on_file is not None:
                        observed = reported_path(entry)
                        if file_hashes is not None:
                            file_hashes[observed] = hashlib.sha256(data).hexdigest()
                        if on_file is not None:
                            on_file(observed, data)
                digest.update(frame(label(entry).encode("utf-8")))
                digest.update(frame(kind.encode("utf-8")))
                digest.update(frame(data))
                if on_progress is not None:
                    on_progress()
    return digest.hexdigest()



def fingerprint_source(
    paths: Iterable[str | os.PathLike[str]], *,
    _normalize_windows_extended_path: Callable[[str], str] = normalize_windows_extended_path,
    _native_path: Callable[[Path], Path] = native_path,
    _fingerprint_entries: Callable[..., str] = fingerprint_entries,
    on_progress: Callable[[], None] | None = None,
    file_hashes: MutableMapping[Path, str] | None = None,
    on_file: Callable[[Path, bytes], None] | None = None,
    _os_module: ModuleType = os,
) -> str:
    """Return a stable sha256 fingerprint over a frozen set of source paths.

    Callers must declare the FULL attributable install input, not just one
    manifest: e.g. both ``src/`` and ``pyproject.toml`` (#5472). Directories
    are walked recursively. Regular files contribute their label and content
    in label order. Each label is relative to the most specific declared root,
    prefixed by that root's path relative to all roots' common ancestor.
    Neither bare basenames (ambiguous across roots) nor absolute paths are
    labels. Relocating a checkout, including across OSes, preserves its digest
    when content and the roots' positions relative to EACH OTHER are unchanged.

    Every label and content record has an explicit 8-byte big-endian length
    prefix. Distinct inputs cannot serialize identically, even when labels or
    content contain arbitrary separator bytes, including NUL.

    Roots are matched longest-path-first, independently of input ORDER; the
    most specific containing root wins. Files reached through overlapping roots
    are hashed once, deduplicated by resolved absolute path. Adding a separately
    declared child root DOES change the declaration: its files are now labeled
    relative to that child rather than its parent, so the digest may change.

    Fails closed: a missing declared root, any unreadable file, or any
    directory that cannot be scanned/stat'd during the walk (e.g. a
    permission-denied subdirectory) raises immediately rather than being
    silently skipped -- a caller relying on this fingerprint for an
    admission decision must never see a best-effort digest over whatever
    happened to remain accessible stand in for "the full attributable input
    was validated". A caller that genuinely needs to fingerprint a
    partially-staged tree must check for that itself before calling this.

    A discovered symlink is its OWN entry: label, target and framed kind,
    never deduplicated away into its target. Nested symlink directories are
    not followed (cycle-unsafe); their pointer identity still detects
    adding, removing or repointing the link. A nested target must lie inside
    a DECLARED root whose separate walk covers its actual bytes, not merely
    inside all roots' common ancestor. Undeclared targets raise; dangling
    targets alone are allowed as identity-only. Lookup errors fail closed.

    Relative targets retain their platform-native text. Covered absolute
    targets are labeled relative to the declared roots' common ancestor
    for relocation invariance. A DECLARED root that is itself a symlink
    always raises: the caller must declare its real resolved content path,
    not just its pointer (#5472). Framed entry kinds keep a file distinct
    from a symlink even when their content and encoded target bytes match.
    """
    import os as _os
    os = _os_module

    if file_hashes:
        raise ValueError("fingerprint_source file_hashes collector must be empty")

    def _lstat_mode(p: Path, context: str) -> int:
        # lstat preserves the final link and propagates unreadable-entry errors;
        # Path.is_* can silently misclassify those failures as absence.
        try:
            return _os.lstat(_native_path(p)).st_mode
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise OSError(f"fingerprint_source: could not stat {p} ({context})") from exc

    resolved_parents: dict[Path, Path] = {}
    reported_roots: dict[Path, Path] = {}

    def _declared_root_path(p: str | PathLike[str]) -> Path:
        # Resolve the PARENT chain (handles relative paths, intermediate
        # symlinks, '..' components) to get a canonical absolute path, but
        # preserve the final component exactly as declared -- never follow
        # it -- so a declared root that is itself a symlink is classified
        # with its own lstat identity below, instead of being silently
        # resolved through to whatever it points at.
        value = os.fspath(p)
        raw = Path(_normalize_windows_extended_path(value) if os.name == "nt" else value)
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        parent = raw.parent
        if parent not in resolved_parents:
            resolved_parents[parent] = Path(_normalize_windows_extended_path(str(
                _native_path(parent).resolve(),
            )))
        # Unlike a symlink name, '..' must match the root normalized by native I/O.
        root = resolved_parents[parent] / raw.name
        if os.name == "nt" and raw.name == "..":
            root = resolved_parents[parent].parent
        spelling = _native_path(root) if os.name == "nt" and value.startswith("\\\\?\\") else root
        reported_roots.setdefault(root, spelling)
        return root

    roots = [_declared_root_path(p) for p in paths]
    root_modes: dict[Path, int] = {}
    for root_path in roots:
        try:
            root_modes[root_path] = _lstat_mode(root_path, "declared root")
        except FileNotFoundError:
            raise FileNotFoundError(
                f"fingerprint_source: declared input path does not exist: {root_path}"
            )
    # Longest-path-first: the most specific containing root always wins,
    # regardless of the order `paths` was passed in.
    ordered_roots = sorted(roots, key=lambda r: len(r.parts), reverse=True)

    # Relative labels preserve order/location independence and distinguish
    # equal basenames under different parents.
    common_root = Path(_os.path.commonpath([str(r) for r in roots]))

    def _root_label(root_path: Path) -> str:
        if root_path == common_root:
            return "."
        return root_path.relative_to(common_root).as_posix()

    def _raise(exc: OSError) -> None:
        raise OSError(f"fingerprint_source: could not scan a directory: {exc}") from exc

    def _require_target_within_declared_roots(candidate: Path) -> None:
        # A NESTED symlink's content is otherwise invisible to this
        # fingerprint (only its own identity/target-pointer is hashed --
        # see docstring); that is only safe when its target's actual
        # bytes are ALREADY covered by one of the declared roots (walked
        # separately, in its own right). A target outside every declared
        # root -- an undeclared external file or directory -- could have
        # its content changed with NO effect on the digest, recreating
        # exactly the content-drift blind spot this function exists to
        # close. Fail closed by rejecting it outright; the caller must
        # either declare the target directly as its own root, or avoid
        # this undeclared external symlink. A dangling target (resolves
        # to nothing at all) is allowed through as identity-only: there is
        # no actual content it could be hiding.
        try:
            target_resolved = Path(_normalize_windows_extended_path(str(
                _native_path(candidate).resolve(strict=True),
            )))
        except FileNotFoundError:
            return  # dangling -- nothing to hide, allow identity-only.
        except OSError as exc:
            # A genuine lookup failure (e.g. permission denied) is NOT
            # the same as "dangling" -- target content may exist and is
            # simply unverifiable here, which must never silently permit
            # an identity-only hash. Fail closed like every other stat
            # failure in this function.
            raise OSError(
                f"fingerprint_source: could not resolve symlink target "
                f"for {candidate}: {exc}"
            ) from exc
        if any(
            target_resolved == r or target_resolved.is_relative_to(r)
            for r in roots
        ):
            return
        raise ValueError(
            f"fingerprint_source: symlink {candidate} points outside "
            f"every declared root (to {target_resolved}) -- its content "
            f"is not otherwise covered by this fingerprint; declare the "
            f"target directly as an additional root, or avoid this "
            f"undeclared external symlink"
        )

    # Each discovered entry is kept under its OWN (never resolved) path, so
    # a symlink is never conflated with whatever it points to. Dict value
    # is "file" for a regular file, or "symlink" for anything that is a
    # symlink (to a file OR a directory). Keying by the un-resolved,
    # as-walked path is also what makes the existing overlapping-root
    # dedup work without an extra resolve() call: the same physical file
    # reached via two overlapping roots is always discovered at the exact
    # same path both times (`root_path` is already resolved up front), so
    # the dict naturally collapses it to one entry.
    entries: dict[Path, str] = {}
    for root_path in roots:
        mode = root_modes[root_path]
        if stat.S_ISLNK(mode):
            # Unlike a NESTED symlink discovered mid-walk (hashed as its
            # own identity -- see docstring), a DECLARED ROOT that is
            # itself a symlink is rejected outright, never silently
            # reduced to link-target text: a root is the caller's own
            # attributable content declaration, so quietly fingerprinting
            # only "where this points" -- while never hashing what is
            # actually read through that path -- would recreate exactly
            # the content-drift blind spot this function exists to close
            # (#5472). The caller must declare the real resolved path (or
            # the symlink's target) instead.
            raise ValueError(
                f"fingerprint_source: declared root is a symlink, not a "
                f"resolved content path: {root_path} -> "
                f"{_os.readlink(_native_path(root_path))!r} -- declare the real target "
                f"path so its actual content is covered"
            )
        elif stat.S_ISDIR(mode):
            # os.walk with onerror=_raise (NOT Path.rglob/is_file, which
            # silently swallow scandir/stat PermissionError on the
            # supported Python versions) so an unreadable subdirectory
            # fails closed instead of being quietly omitted from the
            # fingerprint. followlinks=False is explicit: a symlinked
            # directory is never descended into (see docstring).
            for dirpath, dirnames, filenames in _os.walk(
                _native_path(root_path), onerror=_raise, followlinks=False
            ):
                dirpath = _normalize_windows_extended_path(dirpath)
                for dname in dirnames:
                    candidate = Path(dirpath) / dname
                    cmode = _lstat_mode(candidate, "nested directory entry")
                    if stat.S_ISLNK(cmode):
                        _require_target_within_declared_roots(candidate)
                        entries[candidate] = "symlink"
                    elif not stat.S_ISDIR(cmode):
                        # os.walk placed this name in dirnames, but an
                        # explicit lstat here disagrees it is a plain
                        # directory (e.g. a device/FIFO/socket, or a
                        # TOCTOU race) -- fail closed rather than
                        # silently traverse or skip it.
                        raise ValueError(
                            f"fingerprint_source: unsupported filesystem "
                            f"object type at {candidate} (not a regular "
                            f"file, directory, or symlink)"
                        )
                for name in filenames:
                    candidate = Path(dirpath) / name
                    cmode = _lstat_mode(candidate, "nested file entry")
                    if stat.S_ISLNK(cmode):
                        _require_target_within_declared_roots(candidate)
                        entries[candidate] = "symlink"
                    elif stat.S_ISREG(cmode):
                        entries[candidate] = "file"
                    else:
                        # A FIFO/device/socket/etc. discovered mid-walk
                        # must never be silently omitted from the
                        # fingerprint (a FIFO could also block indefinitely
                        # if ever read) -- only a regular file, directory,
                        # or symlink is a supported attributable input.
                        raise ValueError(
                            f"fingerprint_source: unsupported filesystem "
                            f"object type at {candidate} (not a regular "
                            f"file, directory, or symlink)"
                        )
        elif stat.S_ISREG(mode):
            entries[root_path] = "file"
        else:
            # A declared root that is a FIFO/device/socket/etc. is not a
            # supported attributable input -- fail closed instead of
            # silently reading it as an ordinary file (a FIFO could block
            # indefinitely).
            raise ValueError(
                f"fingerprint_source: unsupported filesystem object type "
                f"at declared root {root_path} (not a regular file, "
                f"directory, or symlink)"
            )

    def _reported_path(entry: Path) -> Path:
        if entry in reported_roots:
            return reported_roots[entry]
        for root_path in ordered_roots:
            if entry.is_relative_to(root_path):
                return reported_roots[root_path] / entry.relative_to(root_path)
        return entry

    def _label(f: Path) -> str:
        # The root's path relative to the common ancestor of every declared
        # root (see `_root_label`), never the root's bare basename (not
        # unique across declared roots) and never its absolute path
        # (would make the digest depend on checkout location).
        for root_path in (f,) if f in root_modes else ordered_roots:
            try:
                rel = f.relative_to(root_path)
            except ValueError:
                continue
            return _root_label(root_path) + "/" + rel.as_posix()
        return f.as_posix()

    return _fingerprint_entries(
        entries, label=_label, root_label=_root_label, roots=roots,
        normalize_link=_normalize_windows_extended_path if os.name == "nt" else str,
        path_type=Path, io_path=_native_path, reported_path=_reported_path,
        file_hashes=file_hashes, on_file=on_file, on_progress=on_progress,
    )
