"""ACP prompt execution, including the original-turn final-send boundary."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from acp import text_block

from .acp_continuation import idle_operation
from .runtime_custody import RuntimeCustodyError

if TYPE_CHECKING:
    from .native_turn_transport import NativeTurnAttempt


class AcpPromptMixin:
    @idle_operation
    async def send_prompt(
        self, text: str, *, native_attempt: NativeTurnAttempt | None = None,
    ) -> dict[str, Any]:
        """Send a prompt and return the full result after its turn completes."""
        if not self._connection or not self._acp_session_id:
            raise RuntimeError("No active ACP session")
        if self._idle_transport is not None and not self.is_running:
            raise RuntimeError("Private idle ACP continuation is not activated")
        if (
            self._native_turn_transport is not None
            and (native_attempt is None or native_attempt.owner is not self._native_turn_transport)
            or native_attempt is not None and native_attempt.owner.client is not self
        ):
            raise RuntimeCustodyError("Selected native transport requires its original turn attempt")

        async with self._prompt_lock:
            self._reset_buffers()
            self._cancel_out_of_turn()
            self._prompt_in_flight = True
            try:
                result = await (
                    self._prompt_or_transport_lost(text) if native_attempt is None
                    else self._prompt_or_transport_lost(text, native_attempt=native_attempt)
                )
                self._stop_reason = result.stop_reason
                self._prompt_complete = True
                self._emit("turn_complete", {"stop_reason": result.stop_reason})
            except Exception as exc:
                self._prompt_error = str(exc)
                self._prompt_complete = True
                self._emit("error", {"message": str(exc)})
                raise
            finally:
                self._prompt_in_flight = False
            return self._build_turn_result()

    async def _prompt_or_transport_lost(
        self, text: str, *, native_attempt: NativeTurnAttempt | None = None,
    ) -> Any:
        """Race the prompt against host transport loss without replaying it."""
        prompt_fut = asyncio.ensure_future(
            native_attempt.send(self, text) if native_attempt is not None else self._prompt_rpc(
                session_id=self._acp_session_id, prompt=[text_block(text)],
            )
        )
        lost_fut = asyncio.ensure_future(self._transport_lost_event.wait())
        try:
            await asyncio.wait({prompt_fut, lost_fut}, return_when=asyncio.FIRST_COMPLETED)
            if prompt_fut.done():
                return prompt_fut.result()
            raise ConnectionResetError(
                f"{self._connection_loss_error or 'ACP transport lost'} "
                "while a prompt was in flight"
            )
        finally:
            for fut in (prompt_fut, lost_fut):
                if not fut.done():
                    fut.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await fut
