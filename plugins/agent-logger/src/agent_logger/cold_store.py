"""agent-logger as an agent-bridge **cold-store provider** (Phase 2c).

Answers agent-bridge's ``session-fetch <session-id> --json`` verb (see
``copilot-extensions`` ``docs/architecture.md``'s cold-store-provider
subsection and ``agent_bridge.cold_store`` for the client side of this
contract): "give me this session's content" for a session the bridge's own
live ledger has nothing for.

agent-logger is the reference provider because it already owns the
session-source seam that knows where a session's raw material lives, across
three tiers, resolved in order:

1. **Local live directory** -- ``~/.copilot/session-state/<id>/``, the same
   root :mod:`agent_logger.segmenter.collate` reads for ``current``/by-id
   session resolution.
2. **On-device compact archive** -- ``<home>/archived-sessions/<id>.tar.gz``
   (:func:`agent_logger.segmenter.collate.session_archive_stores`), a session
   compacted before ever being pushed off this machine.
3. **The locally synced corpus** -- ``session-sync``'s local target root
   (:attr:`agent_logger.config.Config.sync_path`), scanned per
   ``<machine>/session-state/<id>/`` (synced but not yet compacted) and
   ``<machine>/archived/<id>.tar.gz`` (packed at the sync destination) exactly
   as :class:`agent_logger.chronicle.source.SyncedSessionSource` does for the
   chronicler's settle-gated scan.

Every tier goes through :mod:`agent_logger.sessions`' archive-aware reads, so
this module never cares whether the winning ref is a live directory or a
compressed archive -- the same distinction the chronicler's settle window
already gates.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

from agent_logger import sessions
from agent_logger.config import Config, load_config
from agent_logger.segmenter.collate import find_copilot_dir, session_archive_stores
from agent_logger.sessions import SessionRef
from agent_logger.sync.provenance import existing_real_directory, is_link_or_reparse

#: Exit code signaling "no such session" to the agent-bridge daemon -- a
#: legitimate miss, never an error (see ``agent_bridge.cold_store``).
NOT_FOUND_EXIT_CODE = 3


def _local_state_root() -> Path | None:
    try:
        return find_copilot_dir() / sessions.SESSION_STATE_SUBDIR
    except FileNotFoundError:
        return None


def _safe_member_path(path: Path) -> bool:
    """Whether ``path``, if it exists, is a plain regular file (no link/reparse).

    A missing path is safe here -- the subsequent ``read_*`` call simply
    returns ``None`` for it; only an *existing* symlink/reparse point is a
    disclosure risk.
    """
    try:
        mode = path.lstat().st_mode
    except OSError:
        return True
    return stat.S_ISREG(mode) and not is_link_or_reparse(path, mode)


def _is_safe_ref(ref: SessionRef) -> bool:
    """Reject a ref that resolves through a symlink/reparse-point component.

    Mirrors the no-link boundary
    :class:`agent_logger.chronicle.source.SyncedSessionSource` already
    enforces for the same synced-corpus shape (``existing_real_directory`` /
    ``is_link_or_reparse``) -- a symlinked ``sync_path`` entry, machine
    subtree, session directory/archive, or **individual member file** (a live
    ``events.jsonl``, or an archive's uncompressed ``workspace.yaml``/
    ``origin.json`` sidecar) must never let ``session-fetch`` disclose an
    arbitrary readable file. Tarball members are exempt -- they are not
    filesystem paths, and :mod:`agent_logger.sessions` already validates
    them against path-traversal when reading out of the archive.
    """
    if ref.kind == "live":
        if existing_real_directory(ref.path) is None:
            return False
        member_names = (sessions.EVENTS_MEMBER, *sessions.SIDECAR_MEMBERS)
        return all(_safe_member_path(ref.path / name) for name in member_names)
    if ref.store is not None and existing_real_directory(ref.store) is None:
        return False
    try:
        mode = ref.path.lstat().st_mode
    except OSError:
        return False
    if not stat.S_ISREG(mode) or is_link_or_reparse(ref.path, mode):
        return False
    if ref.store is not None:
        sidecar_paths = (
            ref.store / f"{ref.id}.{name}" for name in sessions.SIDECAR_MEMBERS
        )
        if not all(_safe_member_path(p) for p in sidecar_paths):
            return False
    return True


def _resolve_from_synced_corpus(session_id: str, corpus_root: Path) -> SessionRef | None:
    """Search every ``<machine>/`` subtree of the local sync target."""
    real_corpus_root = existing_real_directory(corpus_root)
    if real_corpus_root is None:
        return None
    for raw_machine_dir in sorted(p for p in real_corpus_root.iterdir() if p.is_dir()):
        machine_dir = existing_real_directory(raw_machine_dir)
        if machine_dir is None:
            continue
        ref = sessions.resolve_ref(
            session_id, machine_dir / "session-state", machine_dir / "archived"
        )
        if ref is not None and _is_safe_ref(ref):
            return ref
    return None


def _is_safe_session_id(session_id: str) -> bool:
    """Whether ``session_id`` is a single safe path component.

    ``session_id`` ultimately reaches ``Path`` composition in
    :mod:`agent_logger.sessions` (live-dir join, archive-store join), and the
    daemon forwards an unvalidated request-path parameter as-is (see
    ``agent_bridge`` ``GET /api/v1/sessions/{session_id}``). A value
    containing a path separator, ``..``, or an absolute-path anchor could
    otherwise escape the session roots and read arbitrary files -- reject
    anything that is not exactly one normal path segment, on either POSIX or
    Windows syntax.
    """
    if not session_id or session_id in (".", ".."):
        return False
    normalized = session_id.replace("\\", "/")
    if "/" in normalized:
        return False
    if PureWindowsPath(session_id).anchor or PurePosixPath(session_id).is_absolute():
        return False
    return True


def resolve_session(session_id: str, cfg: Config | None = None) -> SessionRef | None:
    """Resolve ``session_id`` across every tier this host knows about.

    Order: local live directory -> on-device compact archive -> the locally
    synced corpus (packed or unpacked). Returns ``None`` when this host has
    no evidence of the session at all, or when ``session_id`` is not a
    single safe path component (never raises -- an unsafe id is simply
    treated as "not found").
    """
    if not _is_safe_session_id(session_id):
        return None
    state_root = _local_state_root()
    if state_root is not None:
        ref = sessions.resolve_ref(session_id, state_root, *session_archive_stores())
        if ref is not None and _is_safe_ref(ref):
            return ref
    try:
        cfg = cfg or load_config(include_repo=False)
    except Exception:
        return None
    return _resolve_from_synced_corpus(session_id, cfg.sync_path)


def query_reviewer_sessions(
    repo: str,
    pr_number: int,
    *,
    since: str | None = None,
    until: str | None = None,
    cfg: Config | None = None,
) -> list[SessionRef]:
    """Return every session this host can resolve for ``(repo, pr_number)``.

    Backed by :class:`agent_logger.catalog.ReviewCatalogIndex` -- a
    consumer that has exhausted its own live/history-store fallbacks (e.g.
    Intelligence Dampener's reviewer-link chain, once agent-dispatch has
    nothing) reaches this as the durable last tier. ``since``/``until``
    optionally window the result by the annotation's ``recorded_at``
    (inclusive, ISO-8601 string comparison).

    A catalog entry whose session this host cannot resolve (e.g. the
    session lives only on another machine's corpus this host doesn't sync,
    or has aged out of every tier) is silently skipped -- this returns only
    sessions actually resolvable *here*, exactly like :func:`resolve_session`
    treats an unresolvable id as "not found" rather than an error. Results
    preserve the catalog's oldest-first ``recorded_at`` order, deduplicated
    by session id (a session can carry more than one entry for the same
    PR, e.g. distinct roles).
    """
    from agent_logger.catalog import ReviewCatalogIndex

    try:
        cfg = cfg or load_config(include_repo=False)
    except Exception:
        return []
    index = ReviewCatalogIndex(cfg.catalog_db_path)
    entries = index.query(repo, pr_number, since=since, until=until)

    seen: set[str] = set()
    refs: list[SessionRef] = []
    for entry in entries:
        if entry.session_id in seen:
            continue
        seen.add(entry.session_id)
        ref = resolve_session(entry.session_id, cfg=cfg)
        if ref is not None:
            refs.append(ref)
    return refs


def _read_events(ref: SessionRef) -> list[dict]:
    """Parse ``events.jsonl`` into a list of event objects.

    Malformed or non-object lines are skipped rather than failing the whole
    fetch -- one corrupt line must not deny the caller everything else.
    """
    buf = sessions.open_events_lines(ref)
    if buf is None:
        return []
    events: list[dict] = []
    for line in buf:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def build_session_payload(ref: SessionRef) -> dict:
    """Build the ``{"session": {...}, "events": [...]}`` response body."""
    ws = sessions.read_workspace(ref)
    origin = sessions.read_origin(ref)
    session = {
        "session_id": ref.id,
        "cwd": ws.get("cwd") or None,
        "project": origin.get("source_repo") if isinstance(origin, dict) else None,
        "created_at": ws.get("created_at") or None,
        "updated_at": ws.get("updated_at") or None,
    }
    return {"session": session, "events": _read_events(ref)}


def fetch_session_json(session_id: str) -> tuple[int, str]:
    """Resolve ``session_id`` and return ``(exit_code, stdout)``.

    ``exit_code`` follows the cold-store-provider contract: ``0`` found (with
    a JSON ``stdout`` payload), :data:`NOT_FOUND_EXIT_CODE` not found (empty
    ``stdout``).
    """
    ref = resolve_session(session_id)
    if ref is None:
        return NOT_FOUND_EXIT_CODE, ""
    return 0, json.dumps(build_session_payload(ref))
