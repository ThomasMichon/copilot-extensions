"""Replay-safe native creation for an exact provider-owned command instance."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .acquisition_contract import AcquisitionRefused
from .acquisition_executor import executor
from .models import SessionStatus
from .transport import SpawnTarget
from .workspace_creation import creation_fact_options

if TYPE_CHECKING:
    from .db import Database
    from .session_manager import Session, SessionManager


def _placement(target: SpawnTarget) -> dict:
    from .session_manager import _REQUEST_OVERRIDES_KEY

    facts = json.loads(target.to_json())
    facts.pop("caller_worktree", None)
    facts.pop("caller_owner_ref", None)
    if isinstance(facts.get("venue"), dict):
        facts["venue"].pop(_REQUEST_OVERRIDES_KEY, None)
    return facts


def assert_resume_placement(
    db: Database, session_id: str, persisted: SpawnTarget, refreshed: SpawnTarget,
) -> None:
    if db.execute_read(
        "SELECT 1 FROM provider_embodiments WHERE session_id=? LIMIT 1", (session_id,),
    ) and _placement(persisted) != _placement(refreshed):
        from .session_manager import ProviderTargetRefreshError

        raise ProviderTargetRefreshError(
            "Keyed provider placement changed; the original native conversation is retained",
        )


async def start(
    manager: SessionManager, target: SpawnTarget, *, key: str,
    prompt: str | None = None, **options: Any,
) -> tuple[Session, dict[str, Any]]:
    """Journal before launch; uncertainty never permits a second native start."""
    venue = target.venue or {}
    capabilities = venue.get("capabilities")
    if (
        target.type != "command" or not target.spawn_command
        or target.project or target.worktree_id or target.explicit_cwd
        or target.codespace or target.container
        or venue.get("scope") != "provider-instance"
        or venue.get("transport") != "provider-exec" or venue.get("ready") is not True
        or not isinstance(capabilities, dict) or capabilities.get("instance_pinned_exec") is not True
        or any(not isinstance(venue.get(field), str) or not venue[field]
               for field in ("provider", "kind", "target_id", "instance_id"))
    ):
        raise AcquisitionRefused(
            "creation_venue_unsupported",
            "keyed provider creation requires a ready instance-pinned provider-exec target",
            effect="not_started",
        )
    if not isinstance(key, str) or not key.strip() or len(key) > 1024:
        raise AcquisitionRefused("creation_key_invalid", "creation key must be non-empty and at most 1024 characters")
    if prompt is not None:
        raise AcquisitionRefused(
            "creation_prompt_unsupported",
            "provider creation establishes native identity without a first prompt",
            effect="not_started",
        )
    if manager.is_draining:
        raise AcquisitionRefused("creation_draining", "bridge is draining", effect="not_started")
    facts = {
        "target": _placement(target),
        "options": creation_fact_options(options),
    }
    digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    facts_digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    db = manager.db
    with executor(db, f"provider-creation:{digest}"):
        db.execute_write(
            "INSERT OR IGNORE INTO provider_embodiments "
            "(key_digest,facts_digest,state,session_id) VALUES (?,?,'prepared',?)",
            (digest, facts_digest, str(uuid.uuid4())[:12]),
        )
        operation = dict(db.execute_read(
            "SELECT * FROM provider_embodiments WHERE key_digest=?", (digest,),
        )[0])
        if operation["facts_digest"] != facts_digest:
            raise AcquisitionRefused("creation_conflict", "creation key is bound to different provider facts")
        if operation["state"] == "terminal":
            raise AcquisitionRefused("creation_terminal", "original provider conversation was retired")
        replayed = operation["state"] != "prepared"
        if not replayed:
            db.execute_write(
                "UPDATE provider_embodiments SET state='launching' WHERE key_digest=?", (digest,),
            )
            from .session_manager import DaemonDrainingError

            try:
                await manager.start_session(
                    target, creation_session_id=operation["session_id"],
                    **{**options, "caller_id": f"provider-creation:{digest}"},
                )
            except DaemonDrainingError as exc:
                db.execute_write(
                    "UPDATE provider_embodiments SET state='prepared' WHERE key_digest=?", (digest,),
                )
                raise AcquisitionRefused("creation_draining", str(exc), effect="not_started") from exc
        session = manager.get_session(operation["session_id"])
        if session is not None and session.status == SessionStatus.ENDED:
            db.execute_write(
                "UPDATE provider_embodiments SET state='terminal' WHERE key_digest=?", (digest,),
            )
            raise AcquisitionRefused("creation_terminal", "original provider conversation was ended")
        expected = replace(
            target, copilot_args=[*target.copilot_args, *(options.get("copilot_args") or [])],
        )
        if (
            session is None or session.session_id != operation["session_id"]
            or not session.acp_session_id
            or session.status not in {SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.STOPPED}
            or _placement(session.target) != _placement(expected)
            or operation["native_session_id"] is not None
            and operation["native_session_id"] != session.acp_session_id
        ):
            raise AcquisitionRefused("creation_pending", "original provider native start requires reconciliation")
        db.execute_write(
            "UPDATE provider_embodiments SET state='complete',native_session_id=? WHERE key_digest=?",
            (session.acp_session_id, digest),
        )
        return session, {
            "version": 1, "status": "complete", "venue": dict(venue),
            "native_session_id": session.acp_session_id, "turn_index": None,
            "replayed": replayed,
        }
