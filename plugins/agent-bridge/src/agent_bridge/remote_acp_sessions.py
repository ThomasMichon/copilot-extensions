"""Remote sessions enter the existing ledger without a workspace or subprocess."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

from .events import EventLog
from .models import SessionStatus
from .remote_acp_config import REMOTE_ACP_VERSION, RemoteAcpTarget
from .remote_acp_transport import RemoteTransportError
from .transport import SpawnTarget
from . import remote_relationships as relationships
from .relationship_authority import canonical_owner, RelationshipRefused, RelationshipUnknown
if TYPE_CHECKING:
    from .models import StartSessionRequest
    from .session_manager import Session, SessionManager


class RemoteSessionBusy(RuntimeError):
    def __init__(self, session_id: str):
        super().__init__("The selected remote conversation is busy")
        self.detail = {
            "error": "session_conflict",
            "existing_session_id": session_id,
            "message": str(self),
        }


def has_local_launch_options(request: StartSessionRequest) -> bool:
    return any((
        request.worktree_id, request.creation_key, request.provider_instance,
        request.copilot_args, request.env, request.mcp_servers,
        request.model, request.effort, request.parity_fault,
    ))


async def connect_remote_session(
    manager: SessionManager, name: str, *, cwd: str = "/", caller_id: str | None = None,
    caller_session_id: str | None = None, remote_session_id: str | None = None,
    caller_owner_ref: str | None = None,
    session: Session | None = None,
    project: str | None = None,
    reuse: bool = False,
) -> Session:
    if manager.is_draining:
        raise ValueError("Bridge is draining")
    if session is not None and project is None:
        project = session.target.project
    manager._active_acquisitions = getattr(manager, "_active_acquisitions", 0) + 1
    try:
        if caller_owner_ref is not None or session is not None and relationships.owned(session):
            owner = canonical_owner(
                caller_owner_ref if caller_owner_ref is not None else session.target.caller_owner_ref,
            )
            lock = getattr(manager, "_remote_relationship_lock", None)
            if lock is None:
                lock = manager._remote_relationship_lock = asyncio.Lock()
            async with lock:
                return await _attach_remote_session(
                    manager, name, cwd=cwd, caller_id=caller_id, caller_session_id=caller_session_id,
                    remote_session_id=remote_session_id, caller_owner_ref=owner,
                    session=session, project=project, reuse=reuse,
                )
        return await _attach_remote_session(
            manager, name, cwd=cwd, caller_id=caller_id,
            caller_session_id=caller_session_id, remote_session_id=remote_session_id,
            session=session,
            project=project,
            reuse=reuse,
        )
    finally:
        manager._active_acquisitions -= 1


async def _attach_remote_session(
    manager: SessionManager, name: str, *, cwd: str = "/", caller_id: str | None = None,
    caller_session_id: str | None = None, remote_session_id: str | None = None,
    caller_owner_ref: str | None = None,
    session: Session | None = None,
    project: str | None = None,
    reuse: bool = False,
) -> Session:
    from .session_manager import Session

    if manager.is_draining:
        raise ValueError("Bridge is draining")
    catalog = getattr(manager, "_remote_acp_catalog", None)
    if catalog is not None:
        address = name if "://" in name or name.startswith("remote-acp:") else "remote-acp:" + name
        name, spec = await asyncio.to_thread(
            catalog.resolve, address, project=project or (session.target.project if session else None),
        )
    else:
        spec = getattr(manager, "_remote_acp_targets", {}).get(name)
        if spec is None:
            raise ValueError("Unknown configured remote ACP target")
        spec = RemoteAcpTarget.model_validate(spec)
    if manager.is_draining:
        raise ValueError("Bridge is draining")
    snapshot = spec.model_dump(mode="json")
    policy = getattr(manager, "_remote_credential_policy", None)
    auth_factory = policy.bind(spec) if policy is not None else None
    for candidate in manager._sessions.values():
        if candidate is session or candidate.target.type != "remote-acp":
            continue
        same_binding = (
            candidate.target.remote_acp == snapshot
            and candidate.target.project == project
        )
        same_native = remote_session_id is not None and remote_session_id in relationships.native_ids(manager, candidate)
        same_affinity = reuse and caller_id and candidate.caller_id == caller_id and candidate.target.cwd == cwd
        if same_binding and (same_native or same_affinity) and (
            candidate.target.caller_owner_ref != caller_owner_ref
        ):
            raise RelationshipRefused("Remote conversation has a different or unowned host relationship")
    if session is None and caller_owner_ref is not None and remote_session_id is not None:
        for candidate in manager._sessions.values():
            if (
                candidate.target.type == "remote-acp" and candidate.target.remote_acp == snapshot
                and candidate.target.project == project
                and remote_session_id in relationships.native_ids(manager, candidate)
            ):
                if candidate.target.cwd != cwd:
                    raise RelationshipRefused("Existing remote native conversation has a different attachment")
                return await _attach_remote_session(
                    manager, name, cwd=cwd, caller_id=caller_id,
                    caller_session_id=caller_session_id, session=candidate,
                    caller_owner_ref=caller_owner_ref, project=project, reuse=True,
                )
    if session is None and reuse and (caller_id or caller_owner_ref):
        for candidate in reversed(list(manager._sessions.values())):
            if (
                candidate.target.type != "remote-acp"
                or candidate.agent_name != "remote-acp:" + name
                or (
                    candidate.target.caller_owner_ref != caller_owner_ref
                    if caller_owner_ref is not None else candidate.caller_id != caller_id
                )
                or candidate.target.project != project
                or candidate.target.cwd != cwd
                or candidate.status not in {
                    SessionStatus.CREATED, SessionStatus.STARTING, SessionStatus.RUNNING,
                    SessionStatus.IDLE, SessionStatus.STOPPED,
                }
                or (remote_session_id and remote_session_id not in relationships.native_ids(manager, candidate))
                or (candidate.status == SessionStatus.STOPPED and not candidate.acp_session_id and not relationships.owned(candidate))
            ):
                continue
            if RemoteAcpTarget.model_validate(candidate.target.remote_acp).model_dump(mode="json") != snapshot:
                raise ValueError("Remote endpoint/profile/identity changed; explicit new attachment required")
            if candidate.status == SessionStatus.STARTING:
                return candidate
            if relationships.owned(candidate):
                return await _attach_remote_session(
                    manager, name, cwd=cwd, caller_id=caller_id,
                    caller_session_id=caller_session_id, session=candidate,
                    caller_owner_ref=caller_owner_ref, project=project, reuse=True,
                )
            async with candidate._lifecycle_lock:
                return await _attach_remote_session(
                    manager, name, cwd=cwd, caller_id=caller_id,
                    caller_session_id=caller_session_id, session=candidate,
                    caller_owner_ref=caller_owner_ref,
                    project=project, reuse=True,
                )
    previous_spec = (
        RemoteAcpTarget.model_validate(session.target.remote_acp).model_dump(mode="json")
        if session is not None else None
    )
    if session is not None and previous_spec != snapshot:
        raise ValueError("Remote endpoint/profile/identity changed; explicit new attachment required")
    if session is not None and session.target.caller_owner_ref != caller_owner_ref:
        raise RelationshipRefused("Remote conversation owner changed; accepted transfer is required")
    if session is not None and reuse:
        if manager._sessions.get(session.session_id) is not session or session.status not in {
            SessionStatus.CREATED, SessionStatus.STARTING, SessionStatus.RUNNING,
            SessionStatus.IDLE, SessionStatus.STOPPED,
        }:
            raise RemoteTransportError("Remote session ended or was superseded during attachment")
        if session.status == SessionStatus.RUNNING:
            raise RemoteSessionBusy(session.session_id)
        if session.status != SessionStatus.STOPPED:
            await relationships.assert_admission(manager, session)
            return session
        if not session.acp_session_id and not relationships.owned(session):
            raise ValueError("Remote resume requires a stopped durable session")
        remote_session_id = session.acp_session_id
    if session is None:
        if len(manager._sessions) >= manager.MAX_SESSIONS:
            raise ValueError("Bridge session limit exceeded")
        target = SpawnTarget(
            type="remote-acp", cwd=cwd, remote_acp=snapshot, project=project,
            caller_owner_ref=caller_owner_ref,
        )
        session = Session(
            uuid.uuid4().hex[:12], name, target, agent_name="remote-acp:" + name,
            caller_id=caller_id, caller_session_id=caller_session_id,
        )
        session.event_log = EventLog(db=manager._db, session_id=session.session_id)
        if relationships.owned(session):
            manager._db.create_relationship(
                session.session_id, caller_owner_ref, relationships.binding(session), remote_session_id,
                name=name, agent_name=session.agent_name, caller_id=caller_id,
                caller_session_id=caller_session_id, target_json=target.to_json(),
            )
        else:
            manager._db.create_session(
                session_id=session.session_id, name=name, agent_name=session.agent_name,
                caller_id=caller_id, caller_session_id=caller_session_id,
                target_dir=cwd, target_type="remote-acp",
                status=SessionStatus.STARTING.value, now=time.time(),
                target_json=target.to_json(),
            )
        manager._sessions[session.session_id] = session
    if relationships.owned(session):
        async with session._lifecycle_lock:
            if manager._sessions.get(session.session_id) is not session:
                raise RelationshipRefused("Remote relationship was ended during admission")
            return await _connect_remote_client(
                manager, session, spec, cwd, remote_session_id, auth_factory,
            )
    return await _connect_remote_client(manager, session, spec, cwd, remote_session_id, auth_factory)


async def _connect_remote_client(manager, session, spec, cwd, remote_session_id, auth_factory):
    manager._set_background_recovery_enabled(session, False)
    previous = session.client
    transport = getattr(previous, "_remote_transport", None)
    cookies = session._remote_affinity_cookies
    if cookies is None:
        cookies = getattr(transport, "cookies", None)
    if previous is not None and relationships.owned(session):
        try:
            await previous.shutdown()
        except Exception as exc:
            raise RelationshipUnknown("Managed remote transport teardown remains pending") from exc
        if not relationships.client_quiescent(previous):
            raise RelationshipUnknown("Managed remote transport teardown remains pending")
        previous = None
    def on_event(kind: str, data: dict) -> None:
        if session.client is not client or manager._sessions.get(session.session_id) is not session:
            return
        replay = client._loading_session
        if client.acp_session_id:
            session.event_log.set_telemetry_identity(acp_session_id=client.acp_session_id)
        if replay:
            data = {**data, "history_replay": True}
        session.event_log.append(kind, data)
        if replay:
            return
        manager._capture_progress(session, kind, data)
        if kind == "usage_update":
            manager._handle_usage_update(session, data)

    from .acp_client import AcpClient

    client = AcpClient(on_event=on_event)
    session.client = client
    session.status = SessionStatus.STARTING
    session._remote_requested_session_id = remote_session_id

    def require_current() -> None:
        if manager.is_draining:
            raise ValueError("Bridge is draining")
        if (
            manager._sessions.get(session.session_id) is not session
            or session.status != SessionStatus.STARTING
            or session.client is not client or not client.is_running
        ):
            raise RemoteTransportError("Remote session startup was closed or superseded")

    try:
        if relationships.owned(session):
            remote_session_id = await relationships.acquire(manager, session, remote_session_id)
            session._remote_requested_session_id = remote_session_id
        if previous is not None:
            await previous.shutdown()
        if manager.is_draining:
            raise ValueError("Bridge is draining")
        if manager._sessions.get(session.session_id) is not session or session.status != SessionStatus.STARTING:
            raise RemoteTransportError("Remote session ended or was superseded during attachment")
        if auth_factory is None:
            await client.start_remote(spec, cookies=cookies)
        else:
            await client.start_remote(spec, cookies=cookies, auth_factory=auth_factory)
        require_current()
        session._remote_affinity_cookies = getattr(client._remote_transport, "cookies", None)
        session.target.remote_acp_capabilities = {
            "protocol_version": REMOTE_ACP_VERSION,
            "profile": spec.profile,
            "load_session": client._remote_can_load,
            "application_approvals_version": 1,
            "server_requires_explicit_approvals": True,
        }
        manager._db.update_session_target(
            session.session_id, session.target.to_json(), session.target.cwd,
        )
        if remote_session_id:
            if relationships.owned(session) and not session.acp_session_id:
                relationships.enter_attempt(manager, session)
            await asyncio.wait_for(
                client.load_session(
                    cwd=cwd, session_id=remote_session_id, suppress_replay=False,
                ),
                spec.rpc_timeout_seconds,
            )
            sid = remote_session_id
        else:
            if relationships.owned(session):
                relationships.enter_attempt(manager, session)
            sid = await asyncio.wait_for(
                client.new_session(cwd=cwd), spec.rpc_timeout_seconds,
            )
        require_current()
        session.acp_session_id = sid
        manager._db.update_session_acp_id(session.session_id, sid)
        for candidate in manager._sessions.values():
            if (
                candidate is not session and candidate.target.type == "remote-acp"
                and candidate.target.remote_acp == session.target.remote_acp
                and candidate.target.project == session.target.project
                and sid in relationships.native_ids(manager, candidate)
                and (relationships.owned(candidate) or relationships.owned(session))
            ):
                if relationships.owned(session):
                    manager._db.save_relationship_native_id(session.session_id, sid)
                    manager._db.execute_write(
                        "UPDATE remote_acp_relationships SET phase='conflict' WHERE session_id=?",
                        (session.session_id,),
                    )
                raise RelationshipRefused("Remote native conversation already has a Bridge relationship")
        if relationships.owned(session):
            row = manager._db.get_relationship(session.session_id)
            if row is None:
                raise RelationshipUnknown("Managed relationship journal disappeared before confirmation")
            if row["phase"] != "bound" or row["native_id"] != sid:
                await relationships.bind(manager, session, sid)
        session.status = SessionStatus.IDLE
    except BaseException as exc:
        accepted_sid = client.acp_session_id
        if session.client is client and manager._sessions.get(session.session_id) is session:
            if accepted_sid:
                session.acp_session_id = accepted_sid
                manager._db.update_session_acp_id(session.session_id, accepted_sid)
                row = manager._db.get_relationship(session.session_id)
                if row is not None and row["phase"] not in {"conflict", "retired", "closed"}:
                    manager._db.save_relationship_native_id(session.session_id, accepted_sid)
        await client.shutdown()
        if session.client is client and manager._sessions.get(session.session_id) is session:
            session.status = SessionStatus.STOPPED
        if relationships.owned(session) and isinstance(exc, Exception):
            error = exc if isinstance(exc, RelationshipRefused) else RelationshipUnknown(
                "Remote attachment was not confirmed; retained relationship requires reconciliation",
            )
            error.detail = {
                "code": "relationship_outcome_unknown" if isinstance(error, RelationshipUnknown) else "relationship_refused",
                "message": str(error), "session_id": session.session_id,
                "relationship": relationships.inspection(manager, session),
            }
            raise error from None
        raise
    finally:
        if session.client is client and manager._sessions.get(session.session_id) is session:
            if relationships.owned(session) and session.status == SessionStatus.STARTING:
                session.status = SessionStatus.STOPPED
            session._remote_requested_session_id = None
            session.event_log.set_telemetry_identity(acp_session_id=session.acp_session_id)
            manager._db.update_session_status(
                session.session_id, session.status.value, time.time(),
            )
            session.event_log.append("session_state_changed", {"status": session.status.value})
    return session
