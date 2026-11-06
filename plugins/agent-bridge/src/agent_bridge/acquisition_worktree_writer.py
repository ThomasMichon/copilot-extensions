"""Retain the provider-selected writer in the existing keyed acquisition."""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json
import time
from typing import Any

from .acquisition_contract import AcquisitionRefused, AcquisitionRequest
from .acquisition_journal import _digest

CAPABILITY = "retained_acquisition_worktree_writer"
DELIVERY_KEY = "_agent_bridge_worktree_writer_delivery"
JOURNAL_KEY = "_worktree_writer"


@dataclass(frozen=True)
class RetainedWriter:
    request: AcquisitionRequest
    prefix: tuple[str, ...]

    def document(self) -> dict:
        return {"version": 1, "prefix": list(self.prefix)}


_current: ContextVar[RetainedWriter | None] = ContextVar("acquisition_worktree_writer", default=None)


def current() -> RetainedWriter | None:
    """Return only this operation's provider-selected writer."""
    return _current.get()


def retained_document(request: AcquisitionRequest) -> dict | None:
    writer = current()
    if request.worktree_writer is None:
        return None
    if writer is None or writer.request.worktree_writer != request.worktree_writer:
        raise AcquisitionRefused("worktree_writer_unconfirmed", "original acquisition writer is unavailable")
    return writer.document()


def _retained(db: Any, request: AcquisitionRequest) -> dict | None:
    rows = db.execute_read(
        "SELECT a.request_digest,e.data_json FROM acquisition_attempts a "
        "LEFT JOIN acquisition_effects e USING(delivery_id) WHERE a.delivery_id=?",
        (request.delivery_id,),
    )
    if rows:
        if rows[0]["request_digest"] != _digest(request):
            raise AcquisitionRefused("delivery_conflict", "delivery identity was reused for other facts")
        data = json.loads(rows[0]["data_json"] or "{}")
    else:
        prior = db.execute_read(
            "SELECT e.data_json FROM acquisition_attempts a "
            "JOIN acquisition_effects e USING(delivery_id) WHERE a.worktree_id=? "
            "AND a.state<>'refused' "
            "AND json_type(e.data_json,'$._worktree_writer') IS NOT NULL "
            "ORDER BY a.created_at DESC LIMIT 1",
            (request.worktree_id,),
        )
        if not prior:
            if request.worktree_writer is not None and db.execute_read(
                "SELECT 1 FROM acquisition_attempts WHERE worktree_id=? AND state<>'refused' LIMIT 1",
                (request.worktree_id,),
            ):
                raise AcquisitionRefused(
                    "worktree_writer_unconfirmed", "prior acquisition has no original qualified writer",
                )
            return None
        data = json.loads(prior[0]["data_json"])
        original = AcquisitionRequest.model_validate(data["_request"])
        if request.worktree_writer is None or any(
            getattr(request, field) != getattr(original, field)
            for field in ("worktree_writer", "task_id", "target", "mode", "project", "worktree_path")
        ):
            raise AcquisitionRefused(
                "worktree_writer_unconfirmed", "original workspace writer requirement cannot be replaced",
            )
    binding = data.get(JOURNAL_KEY)
    if request.worktree_writer is not None and binding is None:
        raise AcquisitionRefused("worktree_writer_unconfirmed", "original writer binding is missing")
    return binding


def _prefix(binding: object) -> tuple[str, ...]:
    if not (
        isinstance(binding, dict) and set(binding) == {"version", "prefix"}
        and type(binding["version"]) is int and binding["version"] == 1
        and isinstance(binding["prefix"], list) and binding["prefix"]
        and all(isinstance(arg, str) and arg and "\0" not in arg for arg in binding["prefix"])
    ):
        raise AcquisitionRefused("worktree_writer_unconfirmed", "original writer binding is invalid")
    return tuple(binding["prefix"])


async def _qualify(writer: RetainedWriter) -> dict:
    from .acquisition_authority import worktree_command

    expected = writer.request.worktree_writer
    assert expected is not None
    support = await worktree_command(
        ["resource-relationship", "capabilities", "--json"], writer.request.project,
        owner_prefix=writer.prefix,
    )
    contract = support.get("writer_contract")
    if not (
        support.get("schema") == "copilot-extensions.resource-relationship"
        and type(support.get("version")) is int and support["version"] == 1
        and support.get("expected_creation_nonce") is True
        and support.get("expected_writer_identity") is True
        and support.get("writer_scope") == "executing-process"
        and type(support.get("retained_session_writes")) is int
        and support["retained_session_writes"] == 1
        and support.get("writer_identity") == expected.identity.model_dump()
        and isinstance(contract, dict)
        and contract.get("schema") == "copilot-extensions.worktree-record-writer"
        and all(
            type(contract.get(key)) is int and contract[key] == 1
            for key in ("version", "retained_relationships", "creation_nonce")
        )
    ):
        raise AcquisitionRefused(
            "worktree_writer_incompatible",
            "retained executing Worktrees serializer does not satisfy the original requirement",
        )
    return support


@contextlib.asynccontextmanager
async def acquisition_writer(db: Any, request: AcquisitionRequest):
    """Recover the same binding, or select once before the journal's atomic claim."""
    binding = _retained(db, request)
    if request.worktree_writer is None:
        yield
        return
    if request.mode != "acp":
        raise AcquisitionRefused(
            "worktree_writer_mode_unsupported", "retained writer acquisition currently supports ACP only",
            effect="not_started",
        )
    if binding is None:
        from ._peer_launch import ContextRefused
        from .session_lifecycle_cli import _agent_worktrees_pinned_selection

        try:
            prefix, root, _python = _agent_worktrees_pinned_selection()
        except ContextRefused as exc:
            raise AcquisitionRefused("worktree_writer_unavailable", str(exc)) from exc
        if str(root) != request.worktree_writer.identity.installation_root:
            raise AcquisitionRefused("worktree_writer_incompatible", "selected Worktrees installation changed")
    else:
        prefix = _prefix(binding)
    writer = RetainedWriter(request, tuple(prefix))
    await _qualify(writer)
    token = _current.set(writer)
    try:
        yield
    finally:
        _current.reset(token)


def bind_target(target: Any, request: AcquisitionRequest) -> Any:
    """Persist an existing-journal pointer, not another executable registry."""
    if request.worktree_writer is None:
        return target
    from dataclasses import replace

    return replace(target, venue={**(target.venue or {}), DELIVERY_KEY: request.delivery_id})


def require_target(target: Any) -> None:
    """A retained target can launch only inside its original owner scope."""
    delivery = (target.venue or {}).get(DELIVERY_KEY)
    if delivery is None:
        return
    writer = current()
    if writer is None or (
        target.type != "local" or target.worktree_id != writer.request.worktree_id
        or target.cwd != writer.request.worktree_path or target.project != writer.request.project
        or target.spawn_command or target.container or target.codespace or target.remote_acp
    ):
        raise AcquisitionRefused("worktree_writer_unconfirmed", "launch lost its original writer owner")


def require_scope(args: list[str], *, cwd: str | None = None, project: str | None = None) -> None:
    """Prevent inherited task contexts from qualifying another workspace."""
    writer = current()
    if writer is None:
        return
    request = writer.request
    if (cwd is not None and cwd != request.worktree_path) or (
        project is not None and project != request.project
    ):
        raise AcquisitionRefused("worktree_writer_unconfirmed", "owner operation changed workspace")
    for flag in ("--worktree-id", "--worktree", "--worktree-dir", "--cwd"):
        if flag in args:
            expected = request.worktree_path if flag in {"--worktree-dir", "--cwd"} else request.worktree_id
            if args[args.index(flag) + 1] != expected:
                raise AcquisitionRefused("worktree_writer_unconfirmed", "owner operation changed workspace")


def session_write_args(args: list[str]) -> list[str]:
    """Require the admitted CLI itself to serialize delegated session writes."""
    writer = current()
    if writer is None or not args or args[0] not in {"register-session", "bind-session", "link-succession"}:
        return args
    request = writer.request
    scope = {
        **request.worktree_writer.model_dump(), "worktree_id": request.worktree_id,
        "project": request.project, "worktree_path": request.worktree_path,
    }
    return [*args, "--retained-record-writer", json.dumps(scope, sort_keys=True)]


def handoff_state(
    db: Any, predecessor: str, *, create: dict | None = None, stage: str | None = None,
    native_session_id: str | None = None,
):
    """Retain continuation effects in the original acquisition journal."""
    writer = current()
    if writer is None:
        return None
    request = writer.request
    with db._runtime_transaction(write=create is not None or stage is not None, full_sync=True) as conn:
        row = conn.execute(
            "SELECT a.request_digest,e.data_json FROM acquisition_attempts a "
            "JOIN acquisition_effects e USING(delivery_id) WHERE a.delivery_id=?",
            (request.delivery_id,),
        ).fetchone()
        if row is None or row["request_digest"] != _digest(request):
            raise AcquisitionRefused("worktree_writer_unconfirmed", "handoff lost its original acquisition")
        data = json.loads(row["data_json"])
        handoffs = data.setdefault("_writer_handoffs", {})
        retained = handoffs.get(predecessor)
        if create is not None:
            if retained is not None:
                raise AcquisitionRefused("handoff_pending", "original successor must be reconciled")
            retained = handoffs[predecessor] = {**create, "stage": "prepared"}
        if stage is not None:
            if retained is None:
                raise AcquisitionRefused("handoff_pending", "original handoff is unavailable")
            if native_session_id is not None:
                if retained.get("native_session_id", native_session_id) != native_session_id:
                    raise AcquisitionRefused("handoff_pending", "original successor native identity changed")
                retained["native_session_id"] = native_session_id
            retained["stage"] = stage
        if create is not None or stage is not None:
            conn.execute(
                "UPDATE acquisition_effects SET data_json=? WHERE delivery_id=?",
                (json.dumps(data, sort_keys=True), request.delivery_id),
            )
        return retained


def session_writer(method):
    """Retain the same journal owner through later native resume and lineage."""
    @wraps(method)
    async def run(manager, session_id, *args, **kwargs):
        resolved = manager._resolve_ref(session_id) or session_id
        session = manager._sessions.get(resolved)
        delivery = (session.target.venue or {}).get(DELIVERY_KEY) if session else None
        caller = getattr(session, "caller_id", None)
        if delivery is None and isinstance(caller, str) and caller.startswith("acquisition:"):
            delivery = caller.removeprefix("acquisition:")
        if delivery is None:
            return await method(manager, session_id, *args, **kwargs)
        rows = manager._db.execute_read(
            "SELECT e.data_json FROM acquisition_attempts a "
            "JOIN acquisition_effects e USING(delivery_id) WHERE a.delivery_id=?",
            (delivery,),
        )
        try:
            request = AcquisitionRequest.model_validate(json.loads(rows[0]["data_json"])["_request"])
        except (IndexError, ValueError, TypeError, KeyError) as exc:
            raise AcquisitionRefused(
                "worktree_writer_unconfirmed", "native continuation lost its original writer journal",
            ) from exc
        if request.worktree_writer is None:
            if (session.target.venue or {}).get(DELIVERY_KEY) is not None:
                raise AcquisitionRefused("worktree_writer_unconfirmed", "native writer requirement is missing")
            return await method(manager, session_id, *args, **kwargs)
        if (
            session.target.type != "local"
            or request.worktree_id != session.target.worktree_id
            or request.worktree_path != session.target.cwd
            or request.project != session.target.project
        ):
            raise AcquisitionRefused("worktree_writer_unconfirmed", "native continuation changed workspace")
        async with acquisition_writer(manager._db, request):
            from .acquisition_authority import verify_worktree

            await verify_worktree(request)
            if method.__name__ == "resume_session":
                kwargs["allow_recreate"] = False
            return await method(manager, session_id, *args, **kwargs)
    return run


async def registration_pending(manager: Any, session: Any) -> None:
    """Keep an unregistered native dormant without abandoning its host."""
    from .models import SessionStatus

    manager._set_background_recovery_enabled(session, False)
    try:
        await manager._detach_host(session, preserve_host=True)
        if session.client is not None:
            with contextlib.suppress(Exception):
                await session.client.shutdown()
    finally:
        session.client = None
        session.status = SessionStatus.STOPPED
        manager._db.update_session_status(
            session.session_id, SessionStatus.STOPPED.value, time.time(),
        )


@contextlib.contextmanager
def lineage_errors():
    """Keep legacy best-effort lineage, but never hide a required writer failure."""
    try:
        yield
    except Exception:
        if current() is not None:
            raise
