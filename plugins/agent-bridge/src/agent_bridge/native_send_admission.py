"""Idempotency for ordinary native sends, owned by Bridge's turn journal."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NativeSendPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_key: str = Field(min_length=1, max_length=1024)
    expected_native_session_id: str = Field(min_length=1, max_length=1024)
    prompt: str = Field(min_length=1)
    caller_id: str | None = None

    @field_validator("invocation_key", "expected_native_session_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("native send identity must not be blank")
        return value

    def facts(self) -> dict[str, Any]:
        return {
            "version": 1, "operation": "send", "source": "agent-bridge",
            "native_session_id": self.expected_native_session_id, "caller_id": self.caller_id,
        }

    def delivery_id(self) -> str:
        return "native-send:v1:" + self.invocation_key


async def read_receipt(
    manager: Any, session_id: str, request: NativeSendPrompt,
) -> dict[str, Any] | None:
    """Observe historical admission without resuming or submitting on a miss."""
    from .native_keyed_admission import stored_receipt, validate_holder

    session = manager.get_session(session_id)
    if session is None:
        raise KeyError("native_session_unavailable")
    async with session._turn_start_lock:
        validate_holder(manager, session_id, session, request)
        return stored_receipt(manager.db, session_id, request)


async def submit(
    manager: Any, session_id: str, request: NativeSendPrompt,
) -> dict[str, Any]:
    from .native_keyed_admission import stored_receipt
    from .session_manager import DaemonDrainingError
    from .session_status import SessionStatus

    session = manager.get_session(session_id)
    if session is None:
        raise KeyError("native_session_unavailable")
    session_id = session.session_id
    def require_native() -> None:
        if (
            manager.get_session(session_id) is not session
            or session.acp_session_id != request.expected_native_session_id
        ):
            raise ValueError("expected_native_mismatch")

    async def admit(*, may_resume: bool) -> dict[str, Any] | None:
        async with session._turn_start_lock:
            require_native()
            saved = stored_receipt(manager.db, session_id, request)
            if saved is not None:
                return saved
            if manager.is_draining:
                raise DaemonDrainingError("turn")
            if may_resume and session.status == SessionStatus.STOPPED:
                return None
            await manager._submit_prompt_locked(
                session_id, request.prompt, no_resume=True,
                delivery_id=request.delivery_id(), native_admission=request,
            )
            saved = stored_receipt(manager.db, session_id, request)
            if saved is None:
                raise ValueError("native_admission_history_unproven")
            return saved

    saved = await admit(may_resume=True)
    if saved is not None:
        return saved
    # Cold reconnect takes the turn lock itself; resume must not retain it.
    try:
        await manager.resume_session(session_id, drain=False, allow_recreate=False)
    except ValueError:
        # Another sender may have resumed and admitted this exact operation first.
        async with session._turn_start_lock:
            require_native()
            saved = stored_receipt(manager.db, session_id, request)
            if saved is not None:
                return saved
        raise
    saved = await admit(may_resume=False)
    if saved is None:
        raise ValueError("native_admission_history_unproven")
    return saved
