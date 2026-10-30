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
from .queue_records import SpawnReservation, TaskError
from .ordinary_agent_facts import (
    identity, native_conflict, sessionless, validate_created, validate_resumed,
)
from .ordinary_agent_storage import record_created_agent, record_resumed_agent

if TYPE_CHECKING:
    from .client import DispatchClient


def reserve_for_backend(client: DispatchClient, task: dict, supervisor_id: str, *, ordinary: bool, restricted: bool = False) -> dict:
    """Record the selected ordinary backend before its nontransactional create."""
    if restricted:
        from .restricted_delivery import DRIVER
        return client.reserve_spawn(task["id"], reserved_by=supervisor_id, driver=DRIVER)
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


def register_routes(app: Any, queue: Any, bus: Any) -> None:
    from fastapi import HTTPException

    @app.post("/spawn-reservations/{key}/created-agent")
    def record_created(key: str, body: dict[str, Any]) -> dict:
        if (
            not {"task_generation", "agent_info"} <= set(body)
            or set(body) - {"task_generation", "agent_info", "worktree_record", "startup_invocation"}
            or type(body["task_generation"]) is not int
            or ("worktree_record" in body and not isinstance(body["worktree_record"], dict))
        ):
            raise HTTPException(422, detail="invalid created-agent request")
        try:
            reservation = queue.get_reservation(key)
            if reservation and reservation.conversation_retired and not reservation.agent_info:
                from .ordinary_assignment import read_owner
                body = {
                    **body, "worktree_record": read_owner(
                        asdict(reservation), reservation.task_id, timeout=30,
                    ),
                }
            result = asdict(record_created_agent(queue, key, **body))
        except (TaskError, TypeError, BridgeCarriedSessionBusy) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "spawn.agent_created", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/resumed-agent")
    def record_resumed(key: str, body: dict[str, Any]) -> dict:
        if (
            not {"task_generation", "session_info", "worktree_record"} <= set(body)
            or set(body) - {"task_generation", "session_info", "worktree_record", "startup_invocation"}
            or type(body["task_generation"]) is not int
            or not isinstance(body["session_info"], dict)
            or not isinstance(body["worktree_record"], dict)
        ):
            raise HTTPException(422, detail="invalid resumed-agent request")
        try:
            result = asdict(record_resumed_agent(queue, key, **body))
        except (TaskError, TypeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "spawn.agent_resumed", "reservation": result})
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
        invocation = info.get("startup_invocation")
        if invocation is not None and (
            session_info.get("version") != 1
            or session_info.get("invocation_key") != invocation["invocation_key"]
            or session_info.get("native_session_id") != invocation["expected_native_session_id"]
            or session_info.get("queued") is not False
            or session_info.get("admission") != "accepted"
            or type(session_info.get("turn_index")) is not int
            or session_info["turn_index"] != invocation["binding"]["expected_turn_index"]
        ):
            raise TaskError("ordinary admission receipt conflicts with stored invocation")
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
            "ON s.key=m.reservation_key WHERE s.task_id=? AND "
            "(s.worktree=? OR (s.worktree IS NULL AND ? IS NULL)) LIMIT 1",
            (task_id, worktree, worktree),
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
    if (
        reservation.get("reserved_by") != supervisor.supervisor_id
        or reservation.get("release_requested")
        or reservation.get("conclusion_state") == "pending"
    ):
        return 0
    info = reservation.get("agent_info")
    if not info and not (
        reservation.get("creation_key")
        and (
            reservation.get("worktree")
            or reservation.get("allocation_project") and not reservation.get("session_handle")
        )
    ):
        log.error("ordinary reservation %s has unrecorded creation; explicit repair required", reservation["key"])
        return 0
    if task.get("status") != "queued" or task.get("owner") or task.get("hold_reason"):
        return 0
    if time.time() - reservation["updated_at"] < supervisor.reserving_timeout:
        return 0
    if info:
        if reservation.get("conversation_retired") is not False:
            return 0
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
    from .spawn_diagnostics import SpawnDiagnostics

    diagnostics = SpawnDiagnostics(
        task_id=task.get("id"), generation=task["generation"], reservation_key=key,
        secrets={"TASK_PROMPT_SECRET": assignment or "", "STARTUP_PROMPT_SECRET": prompt},
    )
    try:
        with diagnostics.stage("ordinary_read_reservation"):
            reservation = client.get_reservation(key)
    except (DispatchError, TaskError) as exc:
        raise BridgeCarriedSessionBusy(f"ordinary startup reservation cannot be read: {exc}") from exc
    if reservation.get("state") != "reserving" or reservation.get("release_requested"):
        raise BridgeUnavailable("ordinary startup reservation is not admitted")
    info = reservation.get("agent_info")
    replacement = reservation.get("native_replacement_authorization")
    if replacement is not None and (
        not isinstance(replacement, dict) or info or not reservation.get("worktree")
        or replacement.get("contract") != "agent-dispatch/ordinary-native-replacement"
        or type(replacement.get("version")) is not int or replacement["version"] != 1
        or replacement.get("task_id") != task.get("id")
        or replacement.get("task_generation") != task["generation"]
        or not isinstance(replacement.get("replacement_creation_key"), str)
        or not replacement["replacement_creation_key"]
    ):
        raise BridgeCarriedSessionBusy("ordinary native replacement authority is unproven")
    diagnostics.creation_key = reservation.get("creation_key")
    assigned = bool(reservation.get("worktree") and not info) or bool(
        info and info.get("fact_kind") == "resumed"
    )
    if assigned and not info and reservation.get("ordinary_assignment_error"):
        raise BridgeCarriedSessionBusy(reservation["ordinary_assignment_error"])
    from .bridge import registered_agent_project
    harness = registered_agent_project(agent, strict=True)
    project = reservation.get("allocation_project") or harness or project
    from .hosting_policy import require_execution_harness

    if not info and (project or reservation.get("worktree_path") or reservation.get("worktree")):
        require_execution_harness(
            path=reservation.get("worktree_path"), project=project,
            worktree_id=reservation.get("worktree"), timeout=timeout,
        )
    from .workspace_binding import bind_allocation
    from .spawn_factories import SpawnPreparationRetained
    if assigned:
        if not project:
            raise BridgeCarriedSessionBusy("assigned execution project is unavailable")
        # Prove missing historical routing before persisting a candidate namespace.
        reservation["allocation_project"] = project
    elif not info:
        try:
            bind_allocation(client, reservation, project)
        except SpawnPreparationRetained as exc:
            raise CreatedAgentUnrecorded(str(exc)) from exc

    def run(
        argv: list[str], *, prompt_input: str | None = None, read_only: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        from .ordinary_bridge_diagnostics import run as observed_run

        diagnostics.secrets["BRIDGE_INPUT_SECRET"] = prompt_input or ""
        return observed_run(
            prefix, argv, diagnostics=diagnostics, runner=subprocess.run,
            prompt_input=prompt_input, read_only=read_only, timeout=timeout,
        )

    def resumed_info(session_id: str) -> dict:
        result = run(["--json", "status", session_id, "--full"])
        if result.returncode:
            raise BridgeCarriedSessionBusy("resumed ordinary agent full identity is unavailable")
        response = _json_result(result)
        validate_resumed(response, project=project)
        if response["session_id"] != session_id:
            raise TaskError("resumed ordinary agent full identity changed bridge session")
        return {**response, "fact_kind": "resumed"}

    from .ordinary_assignment import current_task, read_owner
    def assignment_record() -> dict:
        current_task(client, task)
        record = read_owner(reservation, task["id"], timeout=timeout)
        if reservation.get("worktree_path") is None:
            reservation["worktree_path"] = record["path"]
        try:
            bind_allocation(client, reservation, record["repo"])
        except SpawnPreparationRetained as exc:
            raise BridgeCarriedSessionBusy(str(exc)) from exc
        return record

    def _store_info() -> None:
        from .ordinary_invocation import prepare, same_facts
        record = None
        if assigned:
            if reservation.get("ordinary_assignment_error"):
                raise BridgeCarriedSessionBusy(reservation["ordinary_assignment_error"])
            record = assignment_record()
        invocation = prepare(task, reservation, info, prompt, worktree_record=record,
                             agent=agent, charter=charter)
        if info.get("fact_kind") == "resumed":
            client.record_resumed_agent(
                key, task_generation=task["generation"], session_info=info,
                worktree_record=record, startup_invocation=invocation,
            )
        else:
            facts = {"task_generation": task["generation"], "agent_info": info,
                     "startup_invocation": invocation}
            if record is not None:
                facts["worktree_record"] = record
            client.record_created_agent(key, **facts)
        readback = client.get_reservation(key).get("agent_info")
        if not isinstance(readback, dict) or not same_facts(readback.get("startup_invocation"), invocation):
            raise CreatedAgentUnrecorded("ordinary invocation storage/read-back capability is unproven")
        info["startup_invocation"] = invocation

    def store_info() -> None:
        with diagnostics.stage("ordinary_store_readback"):
            _store_info()

    if info:
        try:
            actual = identity(info)
            if actual.project != project:
                raise TaskError("retained execution project changed")
        except TaskError as exc:
            raise BridgeUnavailable(f"retained ordinary agent facts are invalid: {exc}") from exc
        session_id = info["session_id"]
        if reservation.get("conversation_retired") is not False:
            raise BridgeCarriedSessionBusy("ordinary allocation conversation is retired or unknown")
        if not isinstance(info.get("startup_invocation"), dict):
            raise BridgeCarriedSessionBusy("historical ordinary invocation is unavailable; no new key permitted")
        try:
            from .ordinary_invocation import native_payload, prepare
            invocation = prepare(task, reservation, info, prompt, agent=agent, charter=charter)
        except (TaskError, DispatchError, TypeError) as exc:
            raise BridgeCarriedSessionBusy(f"ordinary invocation admission fence refused: {exc}") from exc
        receipt_input = json.dumps(native_payload(invocation), ensure_ascii=False)
        try:
            receipt = run(
                ["--json", "send", session_id, "--expected-native-session-id", actual.native,
                 "--native-invocation-file", "-", "--receipt-only", "--no-wait"],
                prompt_input=receipt_input,
                read_only=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise BridgeCarriedSessionBusy(
                f"ordinary historical receipt transport failed; retry retained {session_id}: {exc}",
            ) from exc
        response = _json_result(receipt)
        if receipt.returncode == 0:
            if (response.get("invocation_key") != invocation["invocation_key"]
                    or response.get("native_session_id") != actual.native
                    or response.get("session_id") != session_id
                    or response.get("outcome") != "delivered"
                    or type(response.get("version")) is not int or response["version"] != 1
                    or response.get("queued") is not False or response.get("admission") != "accepted"
                    or type(response.get("turn_index")) is not int
                    or response["turn_index"] != invocation["binding"]["expected_turn_index"]):
                raise BridgeCarriedSessionBusy("historical ordinary receipt is unproven")
            return {"session": f"local-body:{session_id}", "worktree": actual.worktree,
                    "native_session_id": actual.native, "mode": "acp", "session_info": response}
        if response.get("reason") != "native_receipt_missing":
            raise BridgeCarriedSessionBusy("ordinary receipt history is unavailable")
        if reservation.get("conversation_retired") is not False:
            raise BridgeUnavailable("retained ordinary agent has retired or unknown conversation authority")
        try:
            current_task(client, task)
            status_result = run(["--json", "status", session_id], read_only=True)
            if status_result.returncode:
                raise BridgeCarriedSessionBusy("retained ordinary agent status is unproven")
            status = _json_result(status_result).get("status")
            if status == "stopped":
                require_execution_harness(path=actual.path, project=project, timeout=timeout)
            elif status != "idle":
                raise BridgeCarriedSessionBusy(f"retained ordinary agent is not idle: {status}")
            store_info()
            if status == "stopped":
                resumed = run([
                    "--json", "resume", session_id, "--require-agent-hosting", "--strict",
                ])
                response = _json_result(resumed)
                if resumed.returncode or response.get("session_id") != session_id:
                    raise BridgeCarriedSessionBusy("strict ordinary resume did not preserve the retained bridge identity")
                current = resumed_info(session_id)
                if identity(current) != actual:
                    raise TaskError("strict ordinary resume changed retained native/workspace identity")
                if info.get("fact_kind") == "resumed":
                    info = {**current, "startup_invocation": info["startup_invocation"]}
        except (TaskError, DispatchError, OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeCarriedSessionBusy(f"retained ordinary agent resume failed: {exc}") from exc
    else:
        if reservation.get("conversation_retired") is not False and not (
            assigned and reservation.get("inherited_retirement_reconcilable") is True
        ):
            raise BridgeCarriedSessionBusy("ordinary allocation has retired or unknown native history")
        if assigned:
            record = assignment_record()
            known = reservation.get("known_native_session_ids")
            retired = reservation.get("retired_native_session_ids")
            if not isinstance(known, list) or not isinstance(retired, list) or any(
                not isinstance(native, str) or not native for native in [*known, *retired]
            ):
                raise BridgeCarriedSessionBusy("coordinator native bindings are unproven")
            head = record.get("last_session_id")
            if replacement is not None:
                if any(record.get(field) != replacement.get(expected) for field, expected in (
                    ("id", "worktree_id"), ("repo", "project"),
                    ("path", "worktree_path"), ("creation_nonce", "creation_nonce"),
                )):
                    raise BridgeCarriedSessionBusy("native replacement allocation changed")
            elif head:
                if native_conflict(head, known, retired):
                    raise BridgeCarriedSessionBusy("ground head conflicts with known or retired native identity")
                if reservation.get("legacy_acquisition"):
                    raise BridgeCarriedSessionBusy(
                        "legacy allocation has no immutable ordinary invocation; explicit reconciliation required"
                    )
                try:
                    resumed = run([
                        "--json", "resume", reservation["worktree"],
                        "--require-agent-hosting", "--strict",
                    ])
                    if resumed.returncode:
                        raise BridgeCarriedSessionBusy(resumed.stderr or resumed.stdout or "strict worktree resume refused")
                    response = _json_result(resumed)
                    resumed_id = response.get("session_id")
                    if not isinstance(resumed_id, str) or not resumed_id:
                        raise TaskError("strict worktree resume returned no bridge session identity")
                    info = resumed_info(resumed_id)
                    actual = identity(info)
                    if (actual.native != head or actual.worktree != reservation["worktree"]
                            or actual.path != reservation["worktree_path"]):
                        raise TaskError("strict worktree resume changed native/workspace identity")
                    store_info()
                except (TaskError, DispatchError, OSError, subprocess.TimeoutExpired) as exc:
                    raise BridgeCarriedSessionBusy(f"assigned ordinary resume is unproven: {exc}") from exc
                session_id = info["session_id"]
            elif not sessionless(record) or known or retired or reservation.get("session_handle"):
                raise BridgeCarriedSessionBusy("assigned worktree history is unknown; no fresh create permitted")
        elif reservation.get("session_handle") or reservation.get("legacy_acquisition"):
            raise BridgeCarriedSessionBusy("legacy allocation has no exact current workspace/native facts")
        # Bridge's existing repo@venue target selects the harness, not the task's lane.
        if not info:
            target = f"{project}@{agent}" if project and "@" not in agent else agent
            from .ordinary_invocation import KEYED_NATIVE_PROTOCOL
            from .ordinary_replacement import BRIDGE_PROTOCOL
            argv = ["--json", "create", target, "--require-agent-hosting",
                    "--creation-key", replacement["replacement_creation_key"] if replacement else reservation["creation_key"],
                    "--min-daemon-protocol", str(BRIDGE_PROTOCOL if replacement else KEYED_NATIVE_PROTOCOL)]
            if replacement:
                argv += ["--replacement-task", task["id"]]
            if assigned:
                argv += ["--worktree-id", reservation["worktree"]]
            if charter:
                argv += ["--charter", charter]
            try:
                created = run(argv)
                if created.returncode:
                    raise CreatedAgentUnrecorded(created.stderr or created.stdout or "ordinary bridge create failed")
                info = _json_result(created)
                validate_created(info, project=project)
                store_info()
            except (TaskError, DispatchError, TypeError, OSError, subprocess.TimeoutExpired, BridgeCarriedSessionBusy) as exc:
                raise CreatedAgentUnrecorded(
                    f"created agent {(info or {}).get('session_id')!r} could not be recorded; no prompt sent: {exc}"
                ) from exc
            session_id = info["session_id"]
    try:
        from .ordinary_invocation import native_payload
        store_info()
        actual = identity(info)
        invocation = info["startup_invocation"]
        sent = run(["--json", "send", session_id, "--expected-native-session-id",
                    actual.native, "--native-invocation-file", "-", "--no-wait"],
                   prompt_input=json.dumps(native_payload(invocation), ensure_ascii=False))
        if sent.returncode:
            detail = sent.stderr.strip() or sent.stdout.strip() or "no error detail"
            raise BridgeCarriedSessionBusy(f"ordinary bridge send exited {sent.returncode}: {detail}")
        try:
            response = _json_result(sent)
        except BridgeCarriedSessionBusy as exc:
            detail = sent.stderr.strip() or sent.stdout.strip() or "empty response"
            raise BridgeCarriedSessionBusy(f"ordinary send response is malformed: {detail}") from exc
        if (response.get("session_id") != session_id
                or response.get("native_session_id") != actual.native
                or response.get("outcome") != "delivered"
                or response.get("invocation_key") != invocation["invocation_key"]
                or type(response.get("turn_index")) is not int
                or response["turn_index"] != invocation["binding"]["expected_turn_index"]
                or response.get("admission") != "accepted"
                or response.get("queued") is not False):
            raise BridgeCarriedSessionBusy(sent.stderr or "ordinary bridge send outcome is unproven")
    except (TaskError, DispatchError, OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeCarriedSessionBusy(f"ordinary send interrupted; retry retained {session_id}: {exc}") from exc
    return {
        "session": f"local-body:{session_id}", "worktree": actual.worktree,
        "native_session_id": actual.native, "mode": "acp",
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
