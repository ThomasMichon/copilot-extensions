"""Receiving-side checks for a control-authorized ordinary native replacement."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from uuid import UUID

from .acquisition_authority import worktree_command
from .acquisition_contract import AcquisitionRefused

DISPATCH_CONTRACT = "agent-dispatch/ordinary-native-replacement"
OWNER_CONTRACT = "agent-worktrees/conditional-head-replacement"
TARGET_FIELDS = (
    "worktree_id", "worktree_path", "creation_nonce", "expected_head",
    "expected_head_revision", "expected_lifecycle_revision",
)


def refuse(message: str) -> None:
    raise AcquisitionRefused("native_replacement_unconfirmed", message)


def _version(value: object) -> bool:
    return type(value) is int and value == 1


def readiness_command(_args) -> None:
    from . import __main__ as core
    from .protocol import NATIVE_REPLACEMENT_PROTOCOL_VERSION

    supported = core._get_client(ensure=False).daemon_supports(NATIVE_REPLACEMENT_PROTOCOL_VERSION)
    print(json.dumps({
        "contract": DISPATCH_CONTRACT, "version": 1, "supported": supported,
        "required_protocol": NATIVE_REPLACEMENT_PROTOCOL_VERSION,
    }))
    if not supported:
        raise SystemExit(1)


def _validate_grant(grant: dict) -> None:
    strings = (
        "task_id", "authorized_reservation_key", "exclusive_key",
        "allocation_reservation_key", "creation_key", "replacement_creation_key",
        "project", "worktree_id", "worktree_path", "creation_nonce", "expected_head",
    )
    revisions = ("task_generation", "expected_head_revision", "expected_lifecycle_revision")
    if (
        any(not isinstance(grant.get(name), str) or not grant[name].strip() for name in strings)
        or any(type(grant.get(name)) is not int or not 0 < grant[name] < 2**63 for name in revisions)
        or grant["expected_lifecycle_revision"] < grant["expected_head_revision"]
    ):
        refuse("The native replacement grant is incomplete or malformed")
    try:
        canonical = str(UUID(grant["expected_head"]))
    except ValueError as exc:
        raise AcquisitionRefused("native_replacement_unconfirmed", "Invalid predecessor native UUID") from exc
    if canonical != grant["expected_head"]:
        refuse("The predecessor native UUID is not canonical")


@dataclass
class ReplacementAuthority:
    resolver: object
    task_id: str
    project: str
    worktree_id: str
    creation_key: str
    grant: dict

    @classmethod
    async def load(cls, resolver, task_id: str, project: str, worktree_id: str, key: str):
        authority = cls(resolver, task_id, project, worktree_id, key, {})
        authority.grant = await authority.read()
        capabilities = await worktree_command(["replace-dead-head", "capabilities"], project)
        if (
            capabilities.get("contract") != OWNER_CONTRACT or not _version(capabilities.get("version"))
            or not isinstance(capabilities.get("versions"), list)
            or not any(_version(v) for v in capabilities["versions"])
            or not _version(capabilities.get("candidate_observation"))
            or not _version(capabilities.get("preflight"))
        ):
            refuse("The selected Worktrees runtime lacks native replacement checks")
        return authority

    async def read(self) -> dict:
        if not self.resolver:
            refuse("The authenticated dispatch namespace is unavailable")
        try:
            resolved = await self.resolver.resolve_async(f"dispatch:{self.task_id}")
        except (KeyError, ValueError, RuntimeError) as exc:
            raise AcquisitionRefused("dispatch_authority_unavailable", str(exc)) from exc
        venue = resolved.venue or {}
        task, reservation = venue.get("task"), venue.get("acquisition_reservation")
        grant = reservation.get("native_replacement_authorization") if isinstance(reservation, dict) else None
        if not isinstance(task, dict) or not isinstance(reservation, dict) or not isinstance(grant, dict):
            refuse("The current task has no explicit native replacement authorization")
        _validate_grant(grant)
        if (
            venue.get("provider") != "agent-dispatch" or resolved.worktree_id != self.worktree_id
            or task.get("id") != self.task_id or task.get("status") != "queued"
            or task.get("owner") is not None or task.get("owner_session_id") is not None
            or task.get("hold_reason") is not None or task.get("hold_at") is not None
            or type(task.get("generation")) is not int or task["generation"] < 1
            or task["generation"] != grant.get("task_generation")
            or grant.get("contract") != DISPATCH_CONTRACT or not _version(grant.get("version"))
            or grant.get("task_id") != self.task_id
            or grant.get("project") != self.project or grant.get("worktree_id") != self.worktree_id
            or grant.get("replacement_creation_key") != self.creation_key
            or reservation.get("state") != "reserving" or reservation.get("release_requested")
            or reservation.get("conversation_retired") is not True
            or reservation.get("agent_info") or reservation.get("session_handle")
            or reservation.get("ordinary_assignment_error")
            or any(reservation.get(source) != grant.get(destination) for source, destination in (
                ("allocation_project", "project"), ("worktree", "worktree_id"),
                ("worktree_path", "worktree_path"), ("creation_key", "creation_key"),
                ("allocation_reservation_key", "allocation_reservation_key"),
                ("exclusive_key", "exclusive_key"),
            ))
            or task.get("exclusive_key") != grant.get("exclusive_key")
        ):
            refuse("The task generation or retained allocation no longer authorizes replacement")
        if self.grant and self.grant != grant:
            refuse("Native replacement authorization changed")
        return json.loads(json.dumps(grant))

    def target(self) -> dict:
        return {field: self.grant[field] for field in TARGET_FIELDS}

    async def before_launch(self, manager, row: dict, head: str | None) -> None:
        await self.read()
        if (
            row.get("id") != self.worktree_id or row.get("repo") != self.project
            or row.get("path") != self.grant["worktree_path"]
            or row.get("creation_nonce") != self.grant["creation_nonce"]
            or head != self.grant["expected_head"]
            or manager.db.list_fresh_live_sessions(self.worktree_id, now=time.time())
            or manager.db.get_worktree_ownership(self.worktree_id)
            or any(item.target.worktree_id == self.worktree_id for item in manager.list_sessions())
        ):
            refuse("The retained workspace has changed or another Bridge session holds it")
        checked = await worktree_command(
            ["replace-dead-head", "check", "--stdin"], self.project,
            input_json={"contract": OWNER_CONTRACT, "version": 1, **self.target()},
        )
        if (
            not _version(checked.get("version")) or checked.get("contract") != OWNER_CONTRACT
            or checked.get("eligible") is not True or checked.get("target") != self.target()
        ):
            refuse("The ground owner did not confirm the exact stopped predecessor")

    async def observe(self, session) -> dict:
        native, pid = session.acp_session_id, session.pid
        try:
            canonical = str(UUID(native))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AcquisitionRefused("native_replacement_unconfirmed", "Candidate native UUID is unavailable") from exc
        if canonical != native or native == self.grant["expected_head"] or type(pid) is not int or pid <= 0:
            refuse("Candidate native identity or process is unavailable")
        result = await worktree_command([
            "replace-dead-head", "observe", "--worktree-id", self.worktree_id,
            "--session-id", native, "--pid", str(pid),
        ], self.project)
        candidate = result.get("candidate")
        if (
            not _version(result.get("version")) or result.get("contract") != OWNER_CONTRACT
            or not isinstance(candidate, dict) or candidate.get("replacement_session") != native
            or any(candidate.get(field) != self.grant[field] for field in (
                "worktree_id", "worktree_path", "creation_nonce",
            ))
        ):
            refuse("The ground owner did not corroborate this registered candidate")
        return candidate

    def promoted(self, candidate: dict, native: str) -> bool:
        pair = candidate.get("head_transitions")
        return (
            candidate.get("expected_head") == native and isinstance(pair, list) and len(pair) == 2
            and all(isinstance(entry, dict) for entry in pair)
            and pair[0].get("session_id") == self.grant["expected_head"]
            and pair[0].get("revision") == self.grant["expected_head_revision"]
            and pair[1].get("session_id") == native
            and pair[1].get("reason") in {"authorized-dead-head-replacement", "reclaim"}
            and type(pair[1].get("revision")) is int
            and pair[1]["revision"] > self.grant["expected_head_revision"]
            and candidate.get("expected_head_revision") == pair[1]["revision"]
        )

    async def promote(self, session) -> dict:
        await self.read()
        candidate = await self.observe(session)
        native = session.acp_session_id
        if not self.promoted(candidate, native):
            if (
                candidate.get("expected_head") != self.grant["expected_head"]
                or candidate.get("expected_head_revision") != self.grant["expected_head_revision"]
                or type(candidate.get("expected_lifecycle_revision")) is not int
                or candidate["expected_lifecycle_revision"] < self.grant["expected_lifecycle_revision"]
            ):
                refuse("The predecessor changed before native promotion")
            await self.read()
            request = {key: value for key, value in candidate.items() if key != "head_transitions"}
            applied = await worktree_command(
                ["replace-dead-head", "apply", "--permit", "--stdin"], self.project,
                input_json={"contract": OWNER_CONTRACT, "version": 1, **request},
            )
            if (
                not _version(applied.get("version")) or applied.get("contract") != OWNER_CONTRACT
                or applied.get("replaced") is not True or applied.get("previous_head") != self.grant["expected_head"]
                or applied.get("head_session") != native
            ):
                refuse("Native promotion has an unconfirmed outcome; retain the original creation key")
            candidate = await self.observe(session)
            if not self.promoted(candidate, native) or applied.get("head_revision") != candidate["expected_head_revision"]:
                refuse("Native promotion readback changed")
        await self.read()
        # Return plain, stable facts for Dispatch to bind before its first prompt.
        return {
            **json.loads(json.dumps(self.grant)), "replacement_session": native,
            "head_revision": candidate["expected_head_revision"],
        }
