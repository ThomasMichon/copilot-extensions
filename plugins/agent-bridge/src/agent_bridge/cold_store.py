"""Process-boundary client for cold-store providers.

Drives a registered cold-store provider's ``session-fetch`` verb (see
:mod:`agent_bridge.cold_store_sources` for the manifest/registry side of this
contract) exactly as :class:`agent_bridge.agent_registry.CliNamespaceResolver`
drives a namespace provider's ``namespace-list`` / ``namespace-resolve`` --
over a subprocess, never by importing the provider package into the daemon's
own venv.

Exit-code contract for ``<command> session-fetch <session-id> --json``:

- ``0`` -- found. Stdout is a single JSON object: ``{"session": {...},
  "events": [...]}``. ``session`` carries whatever archival identity fields
  the provider knows (``session_id``, ``status``, ``cwd``, ``worktree_id``,
  ``created_at``, ``updated_at``, ...); unrecognized/missing fields are left
  ``None`` by the caller. ``events`` is a JSON array of the session's raw
  event records, oldest first.
- ``3`` -- not found. This provider has nothing for the requested session
  ID -- a legitimate, expected outcome (mapped to ``None``), not an error.
- anything else (non-zero exit, unparseable JSON, spawn failure, timeout) --
  provider error. Logged and treated as "this provider could not answer",
  never raised to the caller -- a misbehaving cold-store provider must not
  break bridge-native session resolution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

from agent_procutil import no_window_flags

log = logging.getLogger(__name__)

#: The one capability this effort's Phase 2b/2c wires end to end. Additional
#: cold-store capabilities may be introduced later without changing this
#: client's shape.
SESSION_FETCH_CAPABILITY = "session-fetch"

#: Exit code a provider uses to signal "no such session" (never an error).
NOT_FOUND_EXIT_CODE = 3


@dataclass(frozen=True)
class ColdStoreSession:
    """A cold-store provider's answer for one session.

    Deliberately loose: a provider may not know every field a live session
    carries. Only ``session_id`` is guaranteed.
    """

    session_id: str
    status: str | None = None
    cwd: str | None = None
    worktree_id: str | None = None
    project: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)


class ColdStoreClient:
    """Drive one registered cold-store provider's ``session-fetch`` verb."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        capability: str = SESSION_FETCH_CAPABILITY,
    ) -> None:
        self._command = command
        self._capability = capability

    @property
    def capability(self) -> str:
        return self._capability

    async def fetch_session(
        self, session_id: str, *, timeout: float = 30.0
    ) -> ColdStoreSession | None:
        """Fetch a session's archival content, or ``None`` if not found/unavailable."""
        argv = [*self._command, "session-fetch", session_id, "--json"]

        def _call() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=no_window_flags(),
            )

        try:
            result = await asyncio.to_thread(_call)
        except Exception:
            log.debug(
                "cold-store provider call failed: %s", argv, exc_info=True,
            )
            return None

        if result.returncode == NOT_FOUND_EXIT_CODE:
            return None
        if result.returncode != 0:
            log.warning(
                "cold-store provider %s returned exit %d for %s: %s",
                self._command[0], result.returncode, session_id,
                (result.stderr or "").strip()[:500],
            )
            return None

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            log.warning(
                "cold-store provider %s returned unparseable output for %s",
                self._command[0], session_id, exc_info=True,
            )
            return None

        if not isinstance(payload, dict):
            log.warning(
                "cold-store provider %s returned a non-object payload for %s",
                self._command[0], session_id,
            )
            return None

        session_data = payload.get("session")
        if not isinstance(session_data, dict):
            log.warning(
                "cold-store provider %s omitted `session` for %s",
                self._command[0], session_id,
            )
            return None

        returned_id = session_data.get("session_id", session_id)
        if returned_id != session_id:
            log.warning(
                "cold-store provider %s returned session_id=%s for requested %s",
                self._command[0], returned_id, session_id,
            )
            return None

        events = payload.get("events", [])
        if not isinstance(events, list) or not all(
            isinstance(e, dict) for e in events
        ):
            log.warning(
                "cold-store provider %s returned malformed events for %s",
                self._command[0], session_id,
            )
            events = []

        return ColdStoreSession(
            session_id=session_id,
            status=session_data.get("status"),
            cwd=session_data.get("cwd"),
            worktree_id=session_data.get("worktree_id"),
            project=session_data.get("project"),
            created_at=session_data.get("created_at"),
            updated_at=session_data.get("updated_at"),
            events=tuple(events),
            raw=session_data,
        )


async def fetch_cold_store_session(
    resolver: Any, session_id: str
) -> ColdStoreSession | None:
    """Ask ``resolver``'s registered cold-store provider for ``session_id``.

    Shared by :meth:`agent_bridge.session_manager.SessionManager.fetch_cold_store_session`
    -- kept here (rather than inline in that already-large module) so the
    ``resolver`` duck-typing (a bare mock in tests may carry no ``cold_store``
    attribute at all) lives next to the client it drives. Returns ``None``
    when ``resolver`` is unset, carries no cold-store registry, or the
    registered provider has nothing for this ID.
    """
    if resolver is None:
        return None
    registry = getattr(resolver, "cold_store", None)
    if registry is None:
        return None
    client = registry.get_client(SESSION_FETCH_CAPABILITY)
    if client is None:
        return None
    return await client.fetch_session(session_id)

