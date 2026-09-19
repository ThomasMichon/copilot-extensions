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
from pathlib import Path

from agent_logger import sessions
from agent_logger.config import Config, load_config
from agent_logger.segmenter.collate import find_copilot_dir, session_archive_stores
from agent_logger.sessions import SessionRef

#: Exit code signaling "no such session" to the agent-bridge daemon -- a
#: legitimate miss, never an error (see ``agent_bridge.cold_store``).
NOT_FOUND_EXIT_CODE = 3


def _local_state_root() -> Path | None:
    try:
        return find_copilot_dir() / sessions.SESSION_STATE_SUBDIR
    except FileNotFoundError:
        return None


def _resolve_from_synced_corpus(session_id: str, corpus_root: Path) -> SessionRef | None:
    """Search every ``<machine>/`` subtree of the local sync target."""
    if not corpus_root.is_dir():
        return None
    for machine_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir()):
        ref = sessions.resolve_ref(
            session_id, machine_dir / "session-state", machine_dir / "archived"
        )
        if ref is not None:
            return ref
    return None


def resolve_session(session_id: str, cfg: Config | None = None) -> SessionRef | None:
    """Resolve ``session_id`` across every tier this host knows about.

    Order: local live directory -> on-device compact archive -> the locally
    synced corpus (packed or unpacked). Returns ``None`` when this host has
    no evidence of the session at all.
    """
    state_root = _local_state_root()
    if state_root is not None:
        ref = sessions.resolve_ref(session_id, state_root, *session_archive_stores())
        if ref is not None:
            return ref
    try:
        cfg = cfg or load_config(include_repo=False)
    except Exception:
        return None
    return _resolve_from_synced_corpus(session_id, cfg.sync_path)


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
