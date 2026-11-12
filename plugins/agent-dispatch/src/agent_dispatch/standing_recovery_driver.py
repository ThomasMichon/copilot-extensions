"""Use the existing native owners to maintain an explicitly granted pairing."""

from __future__ import annotations

import copy
import uuid

from .assignment_local_standing import LocalStandingRelationshipWriter
from .ordinary_writer_admission import OrdinaryWriterAdmission
from .procutil import agent_bridge_launch_prefix
from .queue_records import TaskError
from .queue_standing_recovery import _recovery


class _NativeOwner:
    def __init__(self, owner: LocalStandingRelationshipWriter, request: dict, *, admission: bool):
        self.owner, self.request = owner, request
        self.writer = owner._writer(request, standing=admission, source_required=admission)
        self.bridge = agent_bridge_launch_prefix()
        if not self.bridge:
            raise TaskError("standing recovery original Bridge command is unavailable")

    def bridge_call(self, args: list[str], payload: dict | None = None) -> dict:
        return self.writer._run(["--json", *args], payload, prefix=self.bridge, owner="Bridge")

    def qualify(self, participant: dict, *, automatic: bool = True) -> dict:
        contracts = self.bridge_call(["version", "--contracts-json"]).get("contracts", {})
        if any(
            not isinstance(contracts.get(name), dict)
            or type(contracts[name].get("version")) is not int or contracts[name]["version"] != 1
            or "retained-native-recovery" not in contracts[name].get("capabilities", [])
            for name in ("agent-bridge.resume", "agent-bridge.status")
        ):
            raise TaskError("executing Bridge CLI lacks retained native recovery")
        health = self.bridge_call(["status"])
        capabilities = health.get("capabilities", {})
        if (
            health.get("ready") is not True or health.get("draining") is not False
            or type(capabilities.get("retained_native_session_recovery")) is not int
            or capabilities["retained_native_session_recovery"] != 1
        ):
            raise TaskError("running Bridge owner lacks retained native recovery")
        response = self.bridge_call([
            "status", participant["bridge_session_id"], "--full", "--worktree-writer",
        ])
        observed = response.get("worktree_writer_observation", {})
        if not (
            response.get("session_id") == participant["bridge_session_id"]
            and response.get("acp_session_id") == participant["native_session_id"]
            and observed.get("key_digest") == participant["journal_key"]
            and observed.get("bridge_instance", {}).get("installation_root")
            == participant["bridge_installation"]
            and observed.get("worktree_writer") == participant["writer_requirement"]
            and type(observed.get("retained_native_resume")) is int
            and observed["retained_native_resume"] == 1
        ):
            raise TaskError("standing recovery original native participant changed")
        if automatic and observed.get("automatic_recovery_stopped") is not False:
            raise TaskError("standing recovery native automatic recovery is stopped or unconfirmed")
        return response

    def inspect(self, operation: dict) -> dict:
        participant = operation["native_participant"]
        self.qualify(participant, automatic=False)
        return self.bridge_call([
            "status", participant["bridge_session_id"],
            "--recovery-operation", operation["operation_id"],
        ])

    def dead(self, participant: dict, binding: dict) -> bool:
        response = self.bridge_call(["status", participant["bridge_session_id"], "--full"])
        if response.get("status") != "stopped":
            return False
        self.qualify(participant)
        workspace = binding["workspace"]
        preflight = self.writer._run([
            "--project", workspace["project"], "embody", "--worktree-id", workspace["worktree"],
            "--json", "--bridge-acquisition", binding["owner_session_id"],
            "--require-native-admission", "--dry-run",
        ])
        return (
            preflight.get("ok") is True and preflight.get("dry_run") is True
            and type(preflight.get("native_admission_version")) is int
            and preflight["native_admission_version"] == 1
            and preflight.get("worktree_id") == workspace["worktree"]
            and preflight.get("work_dir") == workspace["path"]
            and preflight.get("resume_session") == binding["owner_session_id"]
        )

    def resume(self, operation: dict) -> dict:
        participant, binding = operation["native_participant"], operation["binding"]
        result = self.bridge_call([
            "resume", participant["bridge_session_id"], "--strict", "--retained-recovery-file", "-",
        ], {
            "worktree_writer": participant["writer_requirement"],
            "retained_recovery": {
                "version": 1, "operation_id": operation["operation_id"],
                "journal_key": participant["journal_key"],
                "bridge_installation": participant["bridge_installation"],
                "native_session_id": binding["owner_session_id"],
                "head_revision": binding["accepted_materialization"]["head"]["revision"],
            },
        })
        receipt = result.get("retained_recovery")
        if not isinstance(receipt, dict):
            raise TaskError("standing recovery original native outcome is unconfirmed")
        return receipt


def recover(queue, task_id: str, *, trigger: str = "confirmed-loss") -> dict:
    task = queue.get(task_id)
    if task is None or task.kind != "standing-charter":
        raise TaskError("operation requires a standing charter task")
    recovery = _recovery(task)
    if recovery is None:
        return {"task_id": task_id, "state": "ungranted"}
    operation = recovery["pending"]
    if operation is None:
        recovery = queue.standing_recovery_authority(task_id)
    elif operation.get("state") == "prepared":
        try:
            with queue._connect() as conn:
                conn.execute("BEGIN")
                queue._standing_recovery_authority(
                    conn, queue._fetch(conn, task_id), queue._now(None), pending=True,
                )
        except TaskError:
            participant = operation["native_participant"]
            queue.settle_standing_recovery(task_id, operation_id=operation["operation_id"], outcome={
                "version": 1, "operation_id": operation["operation_id"], "state": "not-started",
                "journal_key": participant["journal_key"],
                "bridge_session_id": participant["bridge_session_id"],
                "native_session_id": operation["binding"]["owner_session_id"],
            }, prepared_only=True)
            return {"task_id": task_id, "state": "not-started", "operation_id": operation["operation_id"]}
    owner, authority = queue._relationship_selection(task)
    if authority != "local" or not isinstance(owner, LocalStandingRelationshipWriter):
        raise TaskError("retained recovery requires the supported local native standing owner")
    binding = operation["binding"] if operation is not None else recovery["binding"]
    relationship = queue.task_relationship(task_id, event_id=binding["assignment_event_id"])
    if relationship is None or relationship["request"]["request_id"] != binding["request_id"]:
        raise TaskError("standing recovery lost its original assignment")
    request = relationship["request"]
    if operation is not None and operation["state"] in {"invoking", "unknown"}:
        outcome = _NativeOwner(owner, request, admission=False).inspect(operation)
        queue.settle_standing_recovery(task_id, operation_id=operation["operation_id"], outcome=outcome)
        return {"task_id": task_id, "state": outcome["state"], "operation_id": operation["operation_id"]}
    native = _NativeOwner(owner, request, admission=False)
    participant = relationship.get("native_participant")
    if operation is not None:
        participant = operation["native_participant"]
    if not isinstance(participant, dict):
        raise TaskError("standing recovery requires its original native participant")
    if not native.dead(participant, binding):
        return {"task_id": task_id, "state": "live-or-unconfirmed"}
    native.writer = owner._writer(request, standing=True)
    with native.writer.admit_standing(
        request, binding["declaration"], binding["accepted_materialization"]["head"],
    ):
        OrdinaryWriterAdmission(participant["writer_requirement"], {
            "worktree": binding["workspace"]["worktree"],
            "allocation_project": binding["workspace"]["project"],
            "worktree_path": binding["workspace"]["path"],
            "creation_nonce": binding["workspace"]["creation_nonce"],
        })
        if operation is None:
            current = queue.get(task_id)
            operation = queue.prepare_standing_recovery(
                task_id, operation_id=uuid.uuid4().hex,
                grant_id=recovery["request"]["grant_id"],
                expected_revision=current.standing_revision, trigger=trigger,
            )
        operation = queue.claim_standing_recovery(task_id, operation_id=operation["operation_id"])
        unknown = {
            "version": 1, "operation_id": operation["operation_id"], "state": "unknown",
            "journal_key": participant["journal_key"],
            "bridge_session_id": participant["bridge_session_id"],
            "native_session_id": binding["owner_session_id"],
        }
        try:
            outcome = native.resume(copy.deepcopy(operation))
        except Exception:
            queue.settle_standing_recovery(
                task_id, operation_id=operation["operation_id"], outcome=unknown,
            )
            raise
        queue.settle_standing_recovery(task_id, operation_id=operation["operation_id"], outcome=outcome)
        return {"task_id": task_id, "state": outcome["state"], "operation_id": operation["operation_id"]}
