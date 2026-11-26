"""Compose ordinary worktree resume with its original keyed metadata owner."""

from __future__ import annotations

from functools import partial
from typing import Any
import uuid

from fastapi.responses import JSONResponse

from .acquisition_contract import AcquisitionRefused
from .acquisition_executor import executor
from .creation_worktree_writer import JOURNAL_KEY, _binding, _read, retained
from .models import RetainedRecoveryRequest
from .retained_metadata_lineage import adoption, native_for
from .session_status import SessionStatus
from .workspace_transport_identity import identity as transport_identity

CAPABILITY = "retained_worktree_resume"


def _held(message: str) -> AcquisitionRefused:
    return AcquisitionRefused("retained_metadata_held", message)


async def resume(
    request: Any, worktree_id: str, mode: str, *, metadata_only: bool = False,
    expected_native: str | None = None,
) -> JSONResponse | None:
    """Recover discovered keyed metadata before the generic native-import path."""
    from . import retained_metadata_recovery as metadata
    from . import retained_session_recovery as recovery
    from .acquisition_authority import worktree_command
    from .acquisition_writer_inspection import _instance
    from .creation_writer_inspection import inspect_writer
    from .routes import worktrees
    from .routes.sessions import _session_info

    manager = request.app.state.session_manager
    db = manager.db
    rows = db.execute_read(
        "SELECT * FROM workspace_embodiments WHERE creation_writer_json IS NOT NULL "
        "AND json_extract(worktree_json,'$.id')=?",
        (worktree_id,),
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise _held("Multiple keyed journals retain this workspace; its owner must reconcile them")
    original: dict[str, Any] = dict(rows[0])
    boundary = adoption(original)
    if boundary is None and manager.get_session(original["session_id"]) is not None:
        return None
    if boundary is not None:
        for session in manager.list_sessions():
            if (
                session.target.worktree_id == worktree_id
                and (session.target.venue or {}).get(JOURNAL_KEY) == original["key_digest"]
                and session.session_id != boundary["session_id"]
                and native_for(original, session.session_id) == session.acp_session_id
            ):
                return None
    if expected_native is not None and expected_native != original["native_session_id"]:
        raise _held("The selected conversation does not match the retained native owner")
    if mode != "acp":
        raise _held("Missing keyed metadata requires its original ACP recovery owner")
    resolver = request.app.state.resolver
    owner, entry = await getattr(worktrees, "_find_cached_worktree_entry")(
        worktree_id, resolver, cache=worktrees.cache_for_app(request.app),
    )
    scope, _writer = _binding(original)
    config = resolver.agents.get(owner)
    if (
        entry is None or config is None or entry.path != scope.worktree_path
        or config.project != scope.project or transport_identity(resolver, owner) != ("local", None)
    ):
        raise _held("The discovered workspace does not match its original keyed owner")
    def ready() -> bool:
        return bool(getattr(request.app.state, "ready", False))

    if not ready() or manager.is_draining:
        raise _held("Ordinary retained resume requires its ready receiving owner")
    with executor(db, f"ordinary-retained:{worktree_id}"):
        refreshed = _read(db, scope.key_digest)
        if refreshed is None:
            raise _held("The original keyed journal disappeared during ordinary resume")
        original = refreshed
        if expected_native is not None and expected_native != original["native_session_id"]:
            raise _held("The retained native owner changed before recovery")

        async def observe_head() -> dict[str, Any]:
            async with retained(db, original):
                from .acquisition_worktree_writer import current

                selected = current()
                if selected is None:
                    raise _held("Ordinary resume lost its compatible keyed writer")
                head = await partial(worktree_command, owner_prefix=selected.prefix)(
                    ["head-session", "--worktree-id", worktree_id, "--json"], scope.project,
                )
            if (
                head.get("tracked") is not True or head.get("state") != "active"
                or head.get("head_session") != original["native_session_id"]
                or expected_native is not None and head.get("head_session") != expected_native
                or head.get("pending_handoffs") or type(head.get("head_revision")) is not int
            ):
                raise _held("Ordinary resume requires the unchanged authoritative native head")
            return head

        head = await observe_head()
        boundary = adoption(original)
        if boundary is None:
            operation = RetainedRecoveryRequest(
                operation_id=f"ordinary-resume:{uuid.uuid4()}",
                journal_key=scope.key_digest, native_session_id=original["native_session_id"],
                head_revision=head["head_revision"],
                bridge_installation=_instance()["installation_root"],
            )
            session, boundary = await metadata.prepare(
                manager, original["session_id"], operation, scope.worktree_writer, ready,
            )
        else:
            operation = RetainedRecoveryRequest.model_validate(boundary["request"])
            session, boundary = await metadata.prepare(
                manager, original["session_id"], operation, scope.worktree_writer, ready,
            )
        if metadata_only:
            observation = await inspect_writer(manager, session, ready)
            if await observe_head() != head:
                raise _held("The authoritative native head changed during metadata hydration")
            return JSONResponse({
                **_session_info(session, manager).model_dump(mode="json"),
                "metadata_hydration": {"version": 1, "native_execution": False},
                "retained_metadata_adoption": boundary,
                "worktree_writer_observation": observation,
            })
        with executor(db, worktree_id):
            refreshed = _read(db, scope.key_digest)
            if refreshed is None:
                raise _held("Ordinary resume lost its retained operation history")
            original = refreshed
            operations = recovery._operations(original)
            if any(item["state"] not in {"complete", "not-started"} for item in operations.values()):
                raise _held("The retained native operation is unconfirmed; inspect it without replay")
            if (
                session.status in {SessionStatus.IDLE, SessionStatus.RUNNING}
                and session.client is not None and session.client.is_running
            ):
                recovery.require_resume(manager, session)
                outcome = None
            else:
                if session.status != SessionStatus.STOPPED or session.client is not None:
                    raise _held("The retained session is neither confirmed ready nor stopped")
                if operation.operation_id in operations:
                    operation = operation.model_copy(update={
                        "operation_id": f"ordinary-resume:{uuid.uuid4()}",
                        "head_revision": head["head_revision"],
                        "bridge_installation": _instance()["installation_root"],
                    })
                session, outcome = await recovery.resume(
                    manager, session.session_id, operation, scope.worktree_writer, ready,
                )
                if outcome["state"] != "complete":
                    raise _held("The retained native resume is not confirmed complete; inspect its operation")
            if await observe_head() != head:
                raise _held("The authoritative native head changed during ordinary resume")
            observation = await inspect_writer(manager, session, ready)
            return JSONResponse({
                **_session_info(session, manager).model_dump(mode="json"),
                "retained_metadata_adoption": boundary,
                "retained_recovery": outcome,
                "worktree_writer_observation": observation,
            })
