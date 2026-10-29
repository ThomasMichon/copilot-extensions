"""Ordinary native prompt receipts, stored only in the existing turn journal."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .native_send_admission import NativeSendPrompt

def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("native_admission_history_unproven") from exc


class NativeInvocationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    admission_generation: int = Field(ge=0, strict=True)
    original_reservation_key: str = Field(min_length=1)
    creation_key: str = Field(min_length=1)
    allocation_revision: int = Field(ge=1, strict=True)
    expected_turn_index: int = Field(ge=0, strict=True)
    origin_kind: Literal["created", "resumed"]
    worktree_id: str = Field(min_length=1)
    project: str = Field(min_length=1)
    path: str = Field(min_length=1)
    creation_nonce: str = Field(min_length=1)


class KeyedNativePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    invocation_key: str = Field(min_length=1, max_length=1024)
    expected_native_session_id: str = Field(min_length=1, max_length=1024)
    prompt: str = Field(min_length=1)
    binding: NativeInvocationBinding

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported native invocation version")
        return value

    @field_validator("invocation_key", "expected_native_session_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("native invocation identity must not be blank")
        return value

    def facts(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "native_session_id": self.expected_native_session_id,
            "binding": self.binding.model_dump(mode="json"),
        }

    def delivery_id(self) -> str:
        return "ordinary-native:v1:" + self.invocation_key


def stored_receipt(
    db: Any, session_id: str, request: KeyedNativePrompt | NativeSendPrompt, *, connection=None,
) -> dict[str, Any] | None:
    sql = ("SELECT session_id,turn_index,prompt,native_admission_json FROM turns "
           "WHERE acquisition_delivery_id=?")
    rows = (connection.execute(sql, (request.delivery_id(),)).fetchall()
            if connection is not None else db.execute_read(sql, (request.delivery_id(),)))
    if not rows:
        return None
    row = rows[0]
    if type(row["turn_index"]) is not int or row["turn_index"] < 0:
        raise ValueError("native_admission_history_unproven")
    try:
        saved = json.loads(row["native_admission_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError("native_admission_history_unproven") from exc
    if (
        row["session_id"] != session_id or row["prompt"] != request.prompt
        or not isinstance(saved, dict)
        or _canonical(saved.get("facts")) != _canonical(request.facts())
    ):
        raise ValueError("native_invocation_conflict")
    receipt = saved.get("receipt")
    if _canonical(receipt) != _canonical(admission_receipt(session_id, row["turn_index"], request)):
        raise ValueError("native_admission_history_unproven")
    return receipt


def admission_receipt(
    session_id: str, turn_index: int, request: KeyedNativePrompt | NativeSendPrompt,
) -> dict[str, Any]:
    return {
        "version": 1, "session_id": session_id,
        "native_session_id": request.expected_native_session_id,
        "invocation_key": request.invocation_key, "turn_index": turn_index,
        "queued": False, "admission": "accepted",
    }


def validate_holder(
    manager: Any, session_id: str, session: Any, request: KeyedNativePrompt | NativeSendPrompt,
) -> None:
    if (session.session_id != session_id or manager.get_session(session_id) is not session
            or session.acp_session_id != request.expected_native_session_id):
        raise ValueError("expected_native_mismatch")
    if isinstance(request, NativeSendPrompt):
        return
    binding = request.binding.model_dump(mode="json")
    if any(binding[key] != value for key, value in (
        ("worktree_id", session.target.worktree_id),
        ("project", session.target.project), ("path", session.target.cwd),
    )):
        raise ValueError("native_workspace_binding_changed")
    if session.turn_count != binding["expected_turn_index"]:
        raise ValueError("native_admission_history_unproven")
    rows = manager.db.execute_read(
        "SELECT worktree_json,native_session_id,state FROM workspace_embodiments "
        "WHERE session_id=?", (session_id,),
    )
    if not rows and binding["origin_kind"] == "resumed":
        return  # The asynchronous owner read below supplies resumed-workspace proof.
    if len(rows) != 1 or rows[0]["state"] != "complete":
        raise ValueError("native_workspace_history_unproven")
    try:
        allocation = json.loads(rows[0]["worktree_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError("native_workspace_history_unproven") from exc
    if (
        not isinstance(allocation, dict)
        or rows[0]["native_session_id"] != request.expected_native_session_id
        or any(binding[key] != allocation.get(field) for key, field in (
            ("creation_nonce", "creation_nonce"), ("worktree_id", "id"),
            ("project", "repo"), ("path", "path"),
        ))
    ):
        raise ValueError("native_workspace_binding_changed")


async def submit(
    manager: Any, session_id: str, request: KeyedNativePrompt, *, read_only: bool = False,
) -> dict[str, Any] | None:
    session = manager.get_session(session_id)
    if session is None or session.session_id != session_id:
        raise KeyError("native_session_unavailable")
    async with session._turn_start_lock:
        if (
            manager.get_session(session_id) is not session
            or session.acp_session_id != request.expected_native_session_id
        ):
            raise ValueError("expected_native_mismatch")
        binding = request.binding.model_dump(mode="json")
        if any(binding.get(key) != value for key, value in (
            ("worktree_id", session.target.worktree_id),
            ("project", session.target.project), ("path", session.target.cwd),
        )):
            raise ValueError("native_workspace_binding_changed")
        saved = stored_receipt(manager.db, session_id, request)
        if saved is not None or read_only:
            return saved
        if manager.is_draining:
            from .session_manager import DaemonDrainingError
            raise DaemonDrainingError("turn")
        validate_holder(manager, session_id, session, request)
        if binding["origin_kind"] == "resumed":
            from .acquisition_authority import read_worktree_record, worktree_command
            from .worktree_head import parse_head_payload
            record = await read_worktree_record(binding["worktree_id"], binding["project"])
            head = parse_head_payload(json.dumps(await worktree_command(
                ["head-session", "--worktree-id", binding["worktree_id"], "--json"],
                binding["project"],
            )))
            if (
                record.get("status") != "active" or not record.get("sessions_indexed")
                or record.get("execution_leg_opaque") or record.get("session_backend_opaque")
                or record.get("execution_leg") is not None or record.get("session_backend") is not None
                or record.get("creation_nonce") != binding["creation_nonce"]
                or record.get("path") != binding["path"]
                or not head.tracked or head.head_session != request.expected_native_session_id
                or head.occupied and head.state != "active"
            ):
                raise ValueError("native_workspace_binding_changed")
            validate_holder(manager, session_id, session, request)
        await manager._submit_prompt_locked(
            session_id, request.prompt, no_resume=True,
            delivery_id=request.delivery_id(), native_admission=request,
        )
        saved = stored_receipt(manager.db, session_id, request)
        if saved is None:
            raise ValueError("native_admission_history_unproven")
        return saved
