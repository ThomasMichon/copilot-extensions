"""Cold-session archival and archive-aware session access.

session-sync copies each Copilot session as a directory
``session-state/<id>/`` (``events.jsonl`` plus small sidecars). Very old,
inactive sessions are *cold*: safe to compress into a single per-session
archive to reclaim space -- ``events.jsonl`` is ~95% of the bytes and
compresses ~5x.

This module is the seam every reader/writer goes through so a session can be
either a **live directory** or a compressed **archive**, transparently:

- :class:`SessionRef` -- a handle over one session, ``kind`` ``"live"`` or
  ``"archive"``.
- :func:`iter_session_refs` / :func:`resolve_ref` -- discovery across a live
  ``session-state`` root plus any number of archive stores.
- :func:`read_member`, :func:`member_exists`, :func:`read_workspace`,
  :func:`materialize` -- archive-aware reads. Selection metadata
  (``workspace.yaml``/``origin.json``) is kept **uncompressed** beside each
  archive as a *sidecar*, so listing/selecting never hydrates a tarball; only
  content consumers pay a decompress.
- :func:`archive_session` / :func:`restore_session` -- the write path used by
  the on-device and hub compaction flows.

The compression codec is **pluggable** (:data:`CODECS`); the default
``targz`` uses only the standard library, keeping agent-logger free of a
compression dependency. A ``zstd`` codec can be registered later without
touching any call site.
"""

from __future__ import annotations

import atexit
import io
import json
import os
import shutil
import tarfile
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

#: Subdirectory of ``~/.copilot`` holding live session directories.
SESSION_STATE_SUBDIR = "session-state"

#: Sidecar metadata kept uncompressed beside an archive for cheap selection.
#: These are the files the selection/listing/routing paths read; keeping them
#: out of the tarball means those paths never decompress anything.
SIDECAR_MEMBERS: tuple[str, ...] = ("workspace.yaml", "origin.json")

#: The member every real session has; used to tell a session dir from noise.
EVENTS_MEMBER = "events.jsonl"


# ---------------------------------------------------------------------------
# Codecs (pluggable)
# ---------------------------------------------------------------------------

class Codec(ABC):
    """A compression codec: bundle a directory into one archive and read back.

    A codec owns *both* the container (how a directory of files becomes one
    stream) and the compression. The default bundles with ``tar`` and is the
    only place tar/compression specifics live.
    """

    #: Registry name (config ``sync.compact.codec``).
    name: str = "base"
    #: File suffix for an archive produced by this codec (e.g. ``.tar.gz``).
    suffix: str = ""

    @abstractmethod
    def archive_dir(self, src_dir: Path, dest: Path) -> None:
        """Bundle ``src_dir``'s contents into a single archive at ``dest``.

        Members are stored *relative to ``src_dir``* (no leading session-id
        component) so extraction reproduces the session directory directly.
        """

    @abstractmethod
    def read_member(self, archive: Path, member: str) -> bytes | None:
        """Return the bytes of ``member`` from ``archive``, or ``None``."""

    @abstractmethod
    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        """Safely extract every member of ``archive`` under ``dest_dir``."""

    @abstractmethod
    def list_members(self, archive: Path) -> list[str]:
        """Return the archive's member names (files only)."""


def _validate_member_name(name: str) -> str:
    """Reject absolute paths and ``..`` traversal; return a normalized name.

    Guards archive extraction against the ``tar`` path-traversal class of bug
    without relying on ``tarfile.extractall`` (which linters flag): callers
    read members explicitly and write them under a validated relative path.
    """
    norm = name.replace("\\", "/").lstrip("/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"unsafe archive member path: {name!r}")
    return "/".join(parts)


class TarGzCodec(Codec):
    """``tar`` + ``gzip`` bundling, standard-library only (default codec)."""

    name = "targz"
    suffix = ".tar.gz"

    def archive_dir(self, src_dir: Path, dest: Path) -> None:
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(tmp, "w:gz") as tar:
                for path in sorted(src_dir.rglob("*")):
                    if path.is_symlink() or not path.is_file():
                        continue
                    arcname = path.relative_to(src_dir).as_posix()
                    tar.add(path, arcname=arcname, recursive=False)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def read_member(self, archive: Path, member: str) -> bytes | None:
        target = _validate_member_name(member)
        with tarfile.open(archive, "r:gz") as tar:
            try:
                info = tar.getmember(target)
            except KeyError:
                return None
            if not info.isfile():
                return None
            fh = tar.extractfile(info)
            return fh.read() if fh is not None else None

    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            for info in tar.getmembers():
                if not info.isfile():
                    continue
                rel = _validate_member_name(info.name)
                out = dest_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                fh = tar.extractfile(info)
                if fh is None:
                    continue
                with fh, open(out, "wb") as dst:
                    shutil.copyfileobj(fh, dst)

    def list_members(self, archive: Path) -> list[str]:
        with tarfile.open(archive, "r:gz") as tar:
            return [m.name for m in tar.getmembers() if m.isfile()]


#: Registered codecs, keyed by config name. Add ``zstd`` here to enable it.
CODECS: dict[str, Codec] = {c.name: c for c in (TarGzCodec(),)}

#: Archive suffixes recognized during discovery, longest-first so ``.tar.gz``
#: wins over any future ``.gz``.
_ARCHIVE_SUFFIXES: tuple[str, ...] = tuple(
    sorted((c.suffix for c in CODECS.values()), key=len, reverse=True)
)


def get_codec(name: str) -> Codec:
    """Return the registered :class:`Codec` for ``name`` (default ``targz``)."""
    try:
        return CODECS[name]
    except KeyError:
        raise ValueError(
            f"unknown compression codec: {name!r} "
            f"(known: {', '.join(sorted(CODECS))})"
        ) from None


def _codec_for_archive(archive: Path) -> Codec:
    """Pick the codec whose suffix matches ``archive``'s filename."""
    fname = archive.name
    for codec in CODECS.values():
        if codec.suffix and fname.endswith(codec.suffix):
            return codec
    raise ValueError(f"no codec for archive: {archive.name}")


def _archive_stem(archive: Path) -> str:
    """Session id from an archive filename (strip the codec suffix)."""
    fname = archive.name
    for suffix in _ARCHIVE_SUFFIXES:
        if fname.endswith(suffix):
            return fname[: -len(suffix)]
    return archive.stem


# ---------------------------------------------------------------------------
# SessionRef -- a handle over one session (live dir or archive)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionRef:
    """A single session, resolvable whether live or archived.

    Attributes:
        id: the session id (directory / archive stem).
        kind: ``"live"`` (a ``session-state/<id>/`` directory) or
            ``"archive"`` (a ``<store>/<id><suffix>`` compressed bundle).
        path: for ``live``, the session directory; for ``archive``, the
            archive file itself.
        store: for ``archive``, the directory holding the archive and its
            uncompressed sidecars; ``None`` for ``live``.
    """

    id: str
    kind: str
    path: Path
    store: Path | None = None

    @property
    def is_archive(self) -> bool:
        return self.kind == "archive"

    def _sidecar(self, member: str) -> Path | None:
        """Path of the uncompressed sidecar for ``member``, if applicable."""
        if self.store is None or member not in SIDECAR_MEMBERS:
            return None
        return self.store / f"{self.id}.{member}"


def _sidecar_name(session_id: str, member: str) -> str:
    return f"{session_id}.{member}"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _iter_live_refs(state_root: Path) -> Iterator[SessionRef]:
    if not state_root.is_dir():
        return
    for d in state_root.iterdir():
        if d.is_dir() and (d / EVENTS_MEMBER).exists():
            yield SessionRef(id=d.name, kind="live", path=d)


def _iter_archive_refs(store: Path) -> Iterator[SessionRef]:
    if not store.is_dir():
        return
    for f in store.iterdir():
        if not f.is_file():
            continue
        if any(f.name.endswith(s) for s in _ARCHIVE_SUFFIXES):
            yield SessionRef(id=_archive_stem(f), kind="archive", path=f, store=store)


def iter_session_refs(
    state_root: Path, *archive_stores: Path
) -> Iterator[SessionRef]:
    """Yield every session across a live root and any archive stores.

    A live session shadows an archived one with the same id (a session being
    reactivated), so live refs are yielded first and duplicate archive ids are
    skipped.
    """
    seen: set[str] = set()
    for ref in _iter_live_refs(state_root):
        seen.add(ref.id)
        yield ref
    for store in archive_stores:
        for ref in _iter_archive_refs(store):
            if ref.id in seen:
                continue
            seen.add(ref.id)
            yield ref


def resolve_ref(
    session_id: str, state_root: Path, *archive_stores: Path
) -> SessionRef | None:
    """Resolve one session id to a :class:`SessionRef` (live preferred)."""
    live = state_root / session_id
    if live.is_dir() and (live / EVENTS_MEMBER).exists():
        return SessionRef(id=session_id, kind="live", path=live)
    for store in archive_stores:
        for suffix in _ARCHIVE_SUFFIXES:
            cand = store / f"{session_id}{suffix}"
            if cand.is_file():
                return SessionRef(
                    id=session_id, kind="archive", path=cand, store=store
                )
    return None


# ---------------------------------------------------------------------------
# Archive-aware reads
# ---------------------------------------------------------------------------

def read_member(ref: SessionRef, member: str) -> bytes | None:
    """Return raw bytes of ``member`` for ``ref``, or ``None`` if absent.

    For an archive, sidecar members are served from the uncompressed sidecar
    (no decompress); other members are read out of the tarball.
    """
    if ref.kind == "live":
        p = ref.path / member
        return p.read_bytes() if p.is_file() else None
    sidecar = ref._sidecar(member)
    if sidecar is not None and sidecar.is_file():
        return sidecar.read_bytes()
    return _codec_for_archive(ref.path).read_member(ref.path, member)


def read_text(ref: SessionRef, member: str, *, errors: str = "strict") -> str | None:
    """Return ``member`` decoded as UTF-8 text, or ``None`` if absent."""
    raw = read_member(ref, member)
    if raw is None:
        return None
    return raw.decode("utf-8", errors=errors)


def member_exists(ref: SessionRef, member: str) -> bool:
    """Whether ``member`` is present for ``ref`` (sidecar-aware)."""
    if ref.kind == "live":
        return (ref.path / member).is_file()
    sidecar = ref._sidecar(member)
    if sidecar is not None and sidecar.is_file():
        return True
    return member in set(_codec_for_archive(ref.path).list_members(ref.path))


def parse_workspace_text(text: str) -> dict[str, str]:
    """Parse ``workspace.yaml``'s simple ``key: value`` lines into a dict."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def read_workspace(ref: SessionRef) -> dict[str, str]:
    """Return the parsed ``workspace.yaml`` for ``ref`` (``{}`` if missing)."""
    text = read_text(ref, "workspace.yaml", errors="replace")
    return parse_workspace_text(text) if text else {}


def read_origin(ref: SessionRef) -> dict[str, object]:
    """Return the parsed ``origin.json`` for ``ref`` (``{}`` if missing)."""
    text = read_text(ref, "origin.json", errors="replace")
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def materialize(ref: SessionRef) -> Iterator[Path]:
    """Yield a real on-disk session directory for ``ref``.

    For a live session this is the session directory itself (zero cost). For
    an archive it is a temporary directory the archive is extracted into,
    removed on exit -- so content consumers can keep using directory paths.
    """
    if ref.kind == "live":
        yield ref.path
        return
    tmp = Path(tempfile.mkdtemp(prefix=f"agentlog-{ref.id}-"))
    try:
        _codec_for_archive(ref.path).extract_all(ref.path, tmp)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: Temp dirs created by :func:`materialize_path`, removed at interpreter exit.
_MATERIALIZED_TEMPS: list[str] = []


def materialize_path(ref: SessionRef) -> Path:
    """Return a real session-directory Path for ``ref`` (non-scoped variant).

    For a live session, the directory itself. For an archive, a temp directory
    the archive is extracted into, cleaned up at interpreter exit. Prefer
    :func:`materialize` (a context manager) where the lifetime is naturally
    scoped; this suits short-lived CLIs that resolve a session by id, read it,
    and exit -- keeping the archive transparent to path-based call sites.
    """
    if ref.kind == "live":
        return ref.path
    tmp = Path(tempfile.mkdtemp(prefix=f"agentlog-{ref.id}-"))
    try:
        _codec_for_archive(ref.path).extract_all(ref.path, tmp)
    except Exception:
        # Never leak the temp dir if extraction fails (corrupt archive, unknown
        # codec, path-traversal guard) -- it was created before this point.
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _MATERIALIZED_TEMPS.append(str(tmp))
    return tmp


@atexit.register
def _cleanup_materialized_temps() -> None:
    while _MATERIALIZED_TEMPS:
        shutil.rmtree(_MATERIALIZED_TEMPS.pop(), ignore_errors=True)


# ---------------------------------------------------------------------------
# Write path (used by the compaction flows)
# ---------------------------------------------------------------------------

def is_archived(session_id: str, store: Path, codec: str = "targz") -> bool:
    """Whether ``session_id`` already has an archive in ``store``."""
    return (store / f"{session_id}{get_codec(codec).suffix}").is_file()


def archive_session(
    session_dir: Path, store: Path, *, codec: str = "targz"
) -> SessionRef:
    """Compress ``session_dir`` into ``store`` and write selector sidecars.

    Produces ``<store>/<id><suffix>`` (the bundle) plus uncompressed
    ``<store>/<id>.workspace.yaml`` / ``<id>.origin.json`` sidecars for cheap
    selection. Atomic: the archive is written to a temp name and renamed. The
    source directory is **not** removed -- callers decide whether to reclaim it
    once the archive is verified.
    """
    session_id = session_dir.name
    codec_impl = get_codec(codec)
    store.mkdir(parents=True, exist_ok=True)
    archive_path = store / f"{session_id}{codec_impl.suffix}"

    codec_impl.archive_dir(session_dir, archive_path)

    for member in SIDECAR_MEMBERS:
        src = session_dir / member
        if src.is_file():
            dst = store / _sidecar_name(session_id, member)
            tmp = dst.with_name(dst.name + ".tmp")
            tmp.write_bytes(src.read_bytes())
            os.replace(tmp, dst)

    return SessionRef(id=session_id, kind="archive", path=archive_path, store=store)


def verify_archive(ref: SessionRef) -> bool:
    """Sanity-check an archive: readable and contains ``events.jsonl``."""
    if ref.kind != "archive":
        return False
    try:
        members = set(_codec_for_archive(ref.path).list_members(ref.path))
    except (tarfile.TarError, OSError, ValueError):
        return False
    return EVENTS_MEMBER in members


def restore_session(ref: SessionRef, dest_root: Path) -> Path:
    """Extract an archived session back to a live ``dest_root/<id>/`` dir."""
    if ref.kind != "archive":
        raise ValueError("restore_session requires an archive ref")
    dest = dest_root / ref.id
    _codec_for_archive(ref.path).extract_all(ref.path, dest)
    return dest


def remove_archive(ref: SessionRef) -> None:
    """Delete an archive and its sidecars from its store."""
    if ref.kind != "archive" or ref.store is None:
        return
    ref.path.unlink(missing_ok=True)
    for member in SIDECAR_MEMBERS:
        (ref.store / _sidecar_name(ref.id, member)).unlink(missing_ok=True)


def open_events_lines(ref: SessionRef) -> io.StringIO | None:
    """Return ``events.jsonl`` as an in-memory text buffer, or ``None``.

    A convenience for line-oriented consumers that avoids materializing the
    whole session directory when only the event stream is needed.
    """
    raw = read_member(ref, EVENTS_MEMBER)
    if raw is None:
        return None
    return io.StringIO(raw.decode("utf-8", errors="replace"))
