"""Local ordinary admission through the original queue, workspace and Bridge."""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

from .assignment_relationship_owner import WorktreeRelationshipWriter
from .assignment_workspace import AssignmentWorkspace, RelationshipWriter, same_assignment_worker
from .ordinary_agent_facts import identity, sessionless
from .ordinary_pool_startup import local_execution_machine
from .ordinary_writer_admission import OrdinaryWriterAdmission
from .procutil import agent_bridge_launch_prefix
from .queue_records import TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue


class LocalOrdinaryRelationshipWriter:
    """Admit new, locally created ordinary workspaces, not arbitrary participants."""

    def __init__(self, queue: TaskQueue) -> None:
        self.queue = queue

    def _original(self, request: dict) -> tuple[AssignmentWorkspace, dict]:
        if not isinstance(request, dict):
            raise TaskError("ordinary admission requires its original queue request")
        workspace = AssignmentWorkspace.from_dict(request.get("workspace"))
        machine = local_execution_machine()
        if (
            request.get("authority") != "local"
            or not isinstance(machine, str)
            or not same_assignment_worker(f"{machine}/{workspace.worktree}", workspace.worker_id)
            or not isinstance(request.get("reservation_key"), str)
        ):
            raise TaskError("ordinary admission requires its original host-local reservation")
        self.queue._admit_original_relationship_request(request)
        reservation = self.queue.get_reservation(request["reservation_key"])
        if reservation is None:
            raise TaskError("ordinary admission lost its original reservation")
        original = asdict(reservation)
        if (
            reservation.driver != "ordinary-bridge"
            or reservation.worktree_ownership != "created"
            or reservation.allocation_reservation_key != reservation.key
            or not reservation.creation_key
            or not reservation.reserved_by
            or reservation.inherited_worktree is not None
            or not same_assignment_worker(
                f"{reservation.execution_machine}/{reservation.worktree}",
                workspace.worker_id,
            )
            or not same_assignment_worker(
                f"{reservation.creating_host}/{reservation.worktree}",
                workspace.worker_id,
            )
        ):
            raise TaskError(
                "ordinary admission cannot adopt a carried, foreign or unknown workspace"
            )
        OrdinaryWriterAdmission(reservation.worktree_writer_requirement, original)
        return workspace, original

    @staticmethod
    def _record(workspace: AssignmentWorkspace, reservation: dict, record: dict) -> None:
        admission = OrdinaryWriterAdmission(
            reservation["worktree_writer_requirement"], reservation
        )
        admission.check_record(record)
        expected = {
            "task_id": reservation["task_id"],
            "reservation_key": reservation["key"],
            "attempt": reservation["attempt"],
            "driver": reservation["driver"],
            "supervisor": reservation["reserved_by"],
            "creator_machine": reservation["creating_host"],
            "ownership": "created",
        }
        allocation = record.get("dispatch_attempt")
        if isinstance(allocation, dict) and (
            "cohort" in allocation or reservation.get("allocation_cohort_transport") == "cohort-v1"
        ):
            if not isinstance(reservation.get("allocation_cohort"), dict):
                raise TaskError("ordinary admission lacks its original allocation cohort")
            expected["cohort"] = reservation["allocation_cohort"]
        if (
            record.get("dispatch_attempt_opaque") is not False
            or not isinstance(allocation, dict)
            or not same_assignment_worker(
                f"{allocation.get('creator_machine')}/{workspace.worktree}",
                workspace.worker_id,
            )
            or {**allocation, "creator_machine": expected["creator_machine"]} != expected
            or record.get("sessions_indexed") is not True
            or record.get("execution_leg_opaque") is not False
            or record.get("session_backend_opaque") is not False
            or record.get("execution_leg") is not None
            or record.get("session_backend") is not None
            or "checkout_managed" not in record
            or (record["checkout_managed"] is not None and record["checkout_managed"] is not True)
        ):
            raise TaskError("ordinary workspace has unaccounted allocation or writer participants")
        session_ids = record.get("session_ids")
        if (
            not isinstance(session_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in session_ids)
            or type(record.get("session_count")) is not int
            or len(session_ids) != record["session_count"]
        ):
            raise TaskError("ordinary workspace lacks exact indexed native identities")
        info = reservation.get("agent_info")
        if info is None:
            if reservation.get("session_handle") or session_ids or not sessionless(record):
                raise TaskError("ordinary workspace has an uncaptured native participant")
        else:
            actual = identity(info)
            if (
                actual.worktree != workspace.worktree
                or actual.project != workspace.project
                or actual.path != workspace.path
                or session_ids != [actual.native]
                or record.get("last_session_id") not in {None, actual.native}
            ):
                raise TaskError("ordinary workspace has an unaccounted native participant")

    def _writer(self, request: dict) -> WorktreeRelationshipWriter:
        original = copy.deepcopy(request)
        workspace, _ = self._original(original)
        bridge_prefix = agent_bridge_launch_prefix()
        if not bridge_prefix:
            raise TaskError("ordinary admission has no selected Bridge owner")
        selected: WorktreeRelationshipWriter | None = None

        def bridge_run(
            arguments: list[str], *, read_only: bool
        ) -> subprocess.CompletedProcess[str]:
            if not read_only or selected is None:
                raise TaskError("ordinary admission only permits owner reads")
            value = selected._run(arguments, prefix=bridge_prefix, owner="Bridge")
            return subprocess.CompletedProcess(arguments, 0, json.dumps(value), "")

        def admit_request(observed: AssignmentWorkspace, _identity: dict, audited: dict) -> None:
            if observed != workspace or audited != original:
                raise TaskError("ordinary admission differs from its original queue request")
            self._original(audited)

        def admit_workspace(observed: AssignmentWorkspace, _identity: dict) -> None:
            if observed != workspace or selected is None:
                raise TaskError("ordinary workspace changed during admission")
            _, reservation = self._original(original)
            result = selected._run(
                [
                    "--project",
                    workspace.project,
                    "record",
                    "--worktree-id",
                    workspace.worktree,
                    "--json",
                ]
            )
            if result.get("ok") is not True:
                raise TaskError("ordinary workspace owner observation is unavailable")
            self._record(workspace, reservation, result.get("worktree"))
            admission = OrdinaryWriterAdmission(
                reservation["worktree_writer_requirement"],
                reservation,
            )
            if reservation.get("agent_info") is None:
                admission.admit_create(bridge_run)
            else:
                admission.inspect_session(bridge_run, reservation["agent_info"])
            self._original(original)

        selected = WorktreeRelationshipWriter.selected(
            admit_workspace=admit_workspace,
            admit_request=admit_request,
        )
        return selected

    def bind_request(
        self,
        request: dict,
        *,
        authority_guard: Callable[[], None] | None = None,
    ) -> RelationshipWriter:
        return self._writer(request).bind_request(request, authority_guard=authority_guard)

    def inspect_head(self, request: dict, expected_head: dict) -> dict:
        return self._writer(request).inspect_head(request, expected_head)

    def inspect_conclusion(self, request: dict, expected_conclusion: dict) -> dict:
        return self._writer(request).inspect_conclusion(request, expected_conclusion)

    def __call__(self, workspace: AssignmentWorkspace, operation: str, payload: dict) -> dict:
        raise TaskError("ordinary admission requires the bound original queue request")
