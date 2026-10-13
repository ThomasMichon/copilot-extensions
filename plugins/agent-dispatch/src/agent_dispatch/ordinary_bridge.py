"""Ordinary Bridge create/store/send composition; Dispatch stores task facts only."""

from __future__ import annotations

import json
import subprocess
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .bridge import BridgeCarriedSessionBusy, BridgeUnavailable
from .procutil import agent_bridge_launch_prefix, no_window_kwargs
from .queue_records import SpawnReservation, SpawnState, Status, TaskError

if TYPE_CHECKING:
    from .client import DispatchClient


def reserve_for_backend(client: DispatchClient, task: dict, supervisor_id: str, *, ordinary: bool) -> dict:
    """Record the selected ordinary backend before its nontransactional create."""
    if ordinary:
        return client.reserve_spawn(task["id"], reserved_by=supervisor_id, driver="ordinary-bridge",
                                    coordinator_launch=task.get("source") == "ci-repair")
    if task.get("source") == "ci-repair":
        return client.reserve_spawn(task["id"], reserved_by=supervisor_id, coordinator_launch=True)
    return client.reserve_spawn(task["id"], reserved_by=supervisor_id)


class CreatedAgentUnrecorded(BridgeCarriedSessionBusy):
    """Creation may have occurred but storage is unproven; retain the reservation."""


def _json_result(result: subprocess.CompletedProcess[str]) -> dict:
    # Ordinary create/resume can emit progress lines before their JSON result.
    text = result.stdout
    for offset, character in enumerate(text):
        if character == "{":
            try:
                value, end = json.JSONDecoder().raw_decode(text[offset:])
            except ValueError:
                continue
            if isinstance(value, dict) and not text[offset + end:].strip():
                return value
    raise BridgeCarriedSessionBusy("ordinary bridge returned no complete JSON result")


def validate_created(info: dict, *, project: str | None = None) -> dict:
    """Require positive creation readiness and concrete identity, never a default."""
    if not isinstance(info, dict):
        raise TaskError("ordinary bridge creation must be a JSON object")
    connection = info.get("connection")
    if (
        not isinstance(info.get("session_id"), str) or not info["session_id"]
        or info.get("ready") is not True
        or info.get("status") != "awaiting_first_prompt"
        or not isinstance(connection, dict)
        or any(not isinstance(connection.get(field), str) or not connection[field]
               for field in ("worktree_id", "native_session_id", "repo"))
        or (project is not None and connection["repo"] != project)
    ):
        raise TaskError("ordinary bridge creation identity/readiness is unproven")
    return info


def record_created_agent(
    queue: Any, key: str, *, task_generation: int, agent_info: dict,
) -> SpawnReservation:
    """Atomically publish a created, unprompted agent behind the reservation fence."""
    validate_created(agent_info)
    from .acquisition_receipts import ensure
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure(conn)
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        if (
            row is None or row["state"] != SpawnState.RESERVING or row["release_requested"]
            or row["conversation_retired"]
            or task is None or task.generation != task_generation
            or task.hold_reason is not None or task.status != Status.QUEUED or task.owner is not None
            or task.source == "ci-repair"
        ):
            raise TaskError("created-agent reservation admission fence changed")
        if conn.execute(
            "SELECT 1 FROM acquisition_markers m JOIN spawn_reservations s "
            "ON s.key=m.reservation_key WHERE s.task_id=? OR s.worktree=? LIMIT 1",
            (task.id, agent_info["connection"]["worktree_id"]),
        ).fetchone():
            raise TaskError("legacy acquisition authority requires explicit migration; no ordinary send permitted")
        encoded = json.dumps({**agent_info, "task_generation": task_generation}, sort_keys=True)
        if row["agent_info"] and row["agent_info"] != encoded:
            raise TaskError("created-agent identity is already bound")
        worktree = agent_info["connection"]["worktree_id"]
        project = agent_info["connection"]["repo"]
        path = agent_info["connection"].get("target_dir")
        if (
            row["allocation_project"] and row["allocation_project"] != project
            or row["worktree_path"] and row["worktree_path"] != path
            or row["creation_key"] and (not isinstance(path, str) or not path)
        ):
            raise TaskError("created agent does not match retained allocation provenance")
        handle = f"local-body:{agent_info['session_id']}"
        if (row["worktree"] and row["worktree"] != worktree) or (
            row["session_handle"] and row["session_handle"] != handle
        ):
            raise TaskError("created agent does not match retained reservation identity")
        if conn.execute(
            "SELECT 1 FROM spawn_reservations WHERE key<>? AND worktree=? "
            "AND (state IN ('reserving','spawned','cold','releasing') OR conclusion_state='pending')",
            (key, worktree),
        ).fetchone():
            raise TaskError("created-agent worktree has another active owner or cleanup")
        conn.execute(
            "UPDATE spawn_reservations SET worktree=?,session_handle=?,agent_info=?,"
            "worktree_ownership='targeted',driver='ordinary-bridge',allocation_project=?,"
            "worktree_path=?,updated_at=? WHERE key=?",
            (worktree, handle, encoded, project, path, queue._now(None), key),
        )
        result = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(result)


def register_routes(app: Any, queue: Any, bus: Any) -> None:
    from fastapi import HTTPException

    @app.post("/spawn-reservations/{key}/created-agent")
    def record_created(key: str, body: dict[str, Any]) -> dict:
        if set(body) != {"task_generation", "agent_info"} or type(body["task_generation"]) is not int:
            raise HTTPException(422, detail="invalid created-agent request")
        try:
            result = asdict(record_created_agent(queue, key, **body))
        except (TaskError, TypeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "spawn.agent_created", "reservation": result})
        return result


def record_session_info(queue: Any, key: str, session_info: dict) -> SpawnReservation:
    """Store the ordinary send result without inventing agent execution state."""
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT agent_info,session_handle FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        if not row or not row["agent_info"]:
            raise TaskError("ordinary send result requires stored created-agent facts")
        info = json.loads(row["agent_info"])
        if session_info.get("session_id") != info["session_id"]:
            raise TaskError("ordinary send result does not match retained agent")
        info["last_send"] = session_info
        conn.execute("UPDATE spawn_reservations SET agent_info=? WHERE key=?",
                     (json.dumps(info, sort_keys=True), key))
        updated = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(updated)


def legacy_bound(queue: Any, task_id: str, worktree: str | None) -> bool:
    """An old acquisition marker is not implicitly ordinary start authority."""
    with queue._connect() as conn:
        return conn.execute(
            "SELECT 1 FROM acquisition_markers m JOIN spawn_reservations s "
            "ON s.key=m.reservation_key WHERE s.task_id=? OR s.worktree=? LIMIT 1",
            (task_id, worktree),
        ).fetchone() is not None


def carry_agent(conn: Any, task: Any, worktree: str | None, session: str | None,
                ownership: str | None, retired: bool, *, creation_key: str) -> tuple:
    """Carry stored identity after a retry, including tasks without exclusivity."""
    row = conn.execute(
        "SELECT * FROM spawn_reservations WHERE creation_key=? AND agent_info IS NOT NULL "
        "ORDER BY reserved_at DESC, attempt DESC LIMIT 1", (creation_key,),
    ).fetchone()
    if row is None and worktree and session:
        row = conn.execute(
            "SELECT * FROM spawn_reservations WHERE creation_key=? AND worktree=? "
            "AND session_handle=? AND agent_info IS NOT NULL "
            "ORDER BY reserved_at DESC LIMIT 1", (creation_key, worktree, session),
        ).fetchone()
    if not row or retired or row["conversation_retired"] or row["release_requested"] or row["conclusion_state"] == "pending":
        return worktree, session, ownership, None
    if worktree and worktree != row["worktree"]:
        return worktree, session, ownership, None
    info = json.loads(row["agent_info"])
    info["task_generation"] = task.generation
    return row["worktree"], row["session_handle"], "reused", json.dumps(info, sort_keys=True)


def reconcile_created(supervisor: Any, reservation: dict, task: dict) -> int:
    """Recover an ordinary startup using existing reservation/liveness fences."""
    from .client import DispatchError
    log = logging.getLogger(__name__)
    if reservation.get("reserved_by") != supervisor.supervisor_id or reservation.get("release_requested"):
        return 0
    info = reservation.get("agent_info")
    if not info and not (
        reservation.get("creation_key") and reservation.get("allocation_project")
        and not reservation.get("worktree") and not reservation.get("session_handle")
    ):
        log.error("ordinary reservation %s has unrecorded creation; explicit repair required", reservation["key"])
        return 0
    if task.get("status") != "queued" or task.get("owner") or task.get("hold_reason"):
        return 0
    if time.time() - reservation["updated_at"] < supervisor.reserving_timeout:
        return 0
    if info:
        session_id = info["session_id"]
        verdict = supervisor.local_body_verdict_fn(session_id)
        if verdict == "unknown":
            return 0
        if verdict == "live" and supervisor.local_body_activity_fn(session_id) != "IDLE":
            return 0
    try:
        supervisor.client.defer_spawn(
            reservation["key"], detail="ordinary allocation retained for keyed create or same-handle send retry",
        )
    except DispatchError:
        log.exception("could not defer retained ordinary reservation %s", reservation["key"])
        return 0
    return 1


def start(
    task: dict, *, client: Any, agent: str, prompt: str,
    project: str | None = None, charter: str | None = None,
    timeout: float = 180,
) -> dict:
    """Create without a prompt, durably store, then send to the retained handle."""
    prefix = agent_bridge_launch_prefix()
    from .client import DispatchError
    if not prefix:
        raise BridgeUnavailable("ordinary agent-bridge capability is unavailable")
    key = task.get("spawn_reservation_key")
    if not key:
        raise BridgeUnavailable("ordinary bridge startup requires a spawn reservation")
    if client is None:
        raise BridgeUnavailable("ordinary agent-bridge startup requires authenticated coordinator storage")
    if type(task.get("generation")) is not int:
        raise BridgeUnavailable("ordinary startup requires the current task generation")
    assignment = task.get("prompt")
    if assignment is not None and not isinstance(assignment, str):
        raise BridgeUnavailable("ordinary startup task prompt must be a string")
    if assignment:
        prompt = f"{assignment}\n\n{prompt}"
    try:
        reservation = client.get_reservation(key)
    except DispatchError as exc:
        raise BridgeUnavailable(f"ordinary startup reservation cannot be read: {exc}") from exc
    if reservation.get("legacy_acquisition"):
        raise BridgeUnavailable("legacy acquisition authority requires explicit migration before ordinary creation")
    if reservation.get("state") != "reserving" or reservation.get("release_requested"):
        raise BridgeUnavailable("ordinary startup reservation is not admitted")
    info = reservation.get("agent_info")
    from .bridge import registered_agent_project
    harness = registered_agent_project(agent, strict=True)
    project = reservation.get("allocation_project") or harness or project
    from .workspace_binding import bind_allocation
    from .spawn_factories import SpawnPreparationRetained
    try:
        bind_allocation(client, reservation, project)
    except SpawnPreparationRetained as exc:
        raise CreatedAgentUnrecorded(str(exc)) from exc

    def run(argv: list[str], *, prompt_input: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*prefix, *argv], input=prompt_input, capture_output=True,
                              text=True, check=False, timeout=timeout, **no_window_kwargs())

    if info:
        try:
            validate_created(info, project=project)
        except TaskError as exc:
            raise BridgeUnavailable(f"retained ordinary agent facts are invalid: {exc}") from exc
        session_id = info["session_id"]
        if reservation.get("conversation_retired") is not False:
            raise BridgeUnavailable("retained ordinary agent has retired or unknown conversation authority")
        try:
            client.record_created_agent(key, task_generation=task["generation"], agent_info=info)
            status_result = run(["--json", "status", session_id])
            if status_result.returncode:
                raise BridgeCarriedSessionBusy("retained ordinary agent status is unproven")
            status = _json_result(status_result).get("status")
            if status == "stopped":
                resumed = run(["--json", "resume", session_id, "--strict"])
                if resumed.returncode or _json_result(resumed).get("session_id") != session_id:
                    raise BridgeCarriedSessionBusy("strict ordinary resume did not preserve the retained bridge identity")
            elif status != "idle":
                raise BridgeCarriedSessionBusy(f"retained ordinary agent is not idle: {status}")
        except (TaskError, DispatchError, OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeCarriedSessionBusy(f"retained ordinary agent resume failed: {exc}") from exc
    else:
        if reservation.get("worktree") or reservation.get("session_handle"):
            raise BridgeUnavailable("retained allocation has no ordinary created-agent metadata; explicit migration required")
        # Bridge's existing repo@venue target selects the harness, not the task's lane.
        target = f"{project}@{agent}" if project and "@" not in agent else agent
        argv = ["--json", "create", target, "--creation-key", reservation["creation_key"]]
        if charter:
            argv += ["--charter", charter]
        try:
            created = run(argv)
            if created.returncode:
                raise CreatedAgentUnrecorded(created.stderr or created.stdout or "ordinary bridge create failed")
            info = _json_result(created)
            validate_created(info, project=project)
            client.record_created_agent(key, task_generation=task["generation"], agent_info=info)
        except (TaskError, DispatchError, OSError, subprocess.TimeoutExpired, BridgeCarriedSessionBusy) as exc:
            raise CreatedAgentUnrecorded(
                f"created agent {locals().get('info')!r} could not be recorded; no prompt sent: {exc}"
            ) from exc
        session_id = info["session_id"]
    try:
        client.record_created_agent(key, task_generation=task["generation"], agent_info=info)
        prompt = (
            f"{prompt}\n\nUse `--owner-session-id {info['connection']['native_session_id']}` "
            "on the task's owner-gated start command. This is the retained native "
            "conversation identity; claim and lease rules remain unchanged."
        )
        sent = run(["--json", "send", session_id, "--expected-native-session-id",
                    info["connection"]["native_session_id"], "--prompt-file", "-", "--no-wait"],
                   prompt_input=prompt)
        if sent.returncode:
            detail = sent.stderr.strip() or sent.stdout.strip() or "no error detail"
            raise BridgeCarriedSessionBusy(f"ordinary bridge send exited {sent.returncode}: {detail}")
        try:
            response = _json_result(sent)
        except BridgeCarriedSessionBusy as exc:
            detail = sent.stderr.strip() or sent.stdout.strip() or "empty response"
            raise BridgeCarriedSessionBusy(f"ordinary send response is malformed: {detail}") from exc
        if (response.get("session_id") != session_id
                or response.get("native_session_id") != info["connection"]["native_session_id"]
                or response.get("outcome") != "delivered"):
            raise BridgeCarriedSessionBusy(sent.stderr or "ordinary bridge send outcome is unproven")
    except (TaskError, DispatchError, OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeCarriedSessionBusy(f"ordinary send interrupted; retry retained {session_id}: {exc}") from exc
    return {
        "session": f"local-body:{session_id}", "worktree": info["connection"]["worktree_id"],
        "native_session_id": info["connection"]["native_session_id"], "mode": "acp",
        "session_info": response,
    }


def observe(handle: dict, *, timeout: float = 180) -> subprocess.CompletedProcess[str]:
    """Wait after durable send publication; observation never reallocates an agent."""
    prefix = agent_bridge_launch_prefix()
    if not prefix:
        raise BridgeUnavailable("ordinary send was accepted; Bridge observation capability is unavailable")
    session_id = handle["session"].removeprefix("local-body:")
    try:
        return subprocess.run([*prefix, "wait", session_id, "--json"], capture_output=True,
                              text=True, check=False, timeout=timeout, **no_window_kwargs())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeUnavailable(f"ordinary send was accepted; observation failed for {session_id}: {exc}") from exc
