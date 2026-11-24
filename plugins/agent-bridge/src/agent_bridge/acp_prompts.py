"""ACP prompt execution, including the original-turn final-send boundary."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, cast

from acp.schema import TextContentBlock

from .acp_continuation import idle_operation
from .runtime_custody import RuntimeCustodyError

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .native_turn_transport import NativeTurnAttempt


class AcpPromptMixin:
    @idle_operation
    async def send_prompt(
        self, text: str, *, native_attempt: NativeTurnAttempt | None = None,
    ) -> dict[str, Any]:
        """Send a prompt and return the full result after its turn completes."""
        # This mixin is composed into AcpClient, which owns its transport and buffers.
        client = cast("AcpClient", self)
        if not client._connection or not client._acp_session_id:
            raise RuntimeError("No active ACP session")
        if client._idle_transport is not None and not client.is_running:
            raise RuntimeError("Private idle ACP continuation is not activated")
        if client._acquisition_launch is not None and client._native_turn_transport is None:
            raise RuntimeCustodyError("Original acquisition turn transport has not been captured")
        if (
            client._native_turn_transport is not None
            and (native_attempt is None or native_attempt.owner is not client._native_turn_transport)
            or native_attempt is not None and native_attempt.owner.client is not client
        ):
            raise RuntimeCustodyError("Selected native transport requires its original turn attempt")

        async with client._prompt_lock:
            client._reset_buffers()
            client._cancel_out_of_turn()
            client._prompt_in_flight = True
            try:
                result = await (
                    client._prompt_or_transport_lost(text) if native_attempt is None
                    else client._prompt_or_transport_lost(text, native_attempt=native_attempt)
                )
                client._stop_reason = result.stop_reason
                client._prompt_complete = True
                client._emit("turn_complete", {"stop_reason": result.stop_reason})
            except Exception as exc:
                client._prompt_error = str(exc)
                client._prompt_complete = True
                client._emit("error", {"message": str(exc)})
                raise
            finally:
                client._prompt_in_flight = False
            return client._build_turn_result()

    async def _prompt_or_transport_lost(
        self, text: str, *, native_attempt: NativeTurnAttempt | None = None,
    ) -> Any:
        """Race the prompt against host transport loss without replaying it."""
        client = cast("AcpClient", self)
        prompt_fut = asyncio.ensure_future(
            native_attempt.send(client, text) if native_attempt is not None else client._prompt_rpc(
                session_id=client._acp_session_id, prompt=[TextContentBlock(type="text", text=text)],
            )
        )
        lost_fut = asyncio.ensure_future(client._transport_lost_event.wait())
        try:
            await asyncio.wait({prompt_fut, lost_fut}, return_when=asyncio.FIRST_COMPLETED)
            if prompt_fut.done():
                return prompt_fut.result()
            raise ConnectionResetError(
                f"{client._connection_loss_error or 'ACP transport lost'} "
                "while a prompt was in flight"
            )
        finally:
            for fut in (prompt_fut, lost_fut):
                if not fut.done():
                    fut.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await fut
