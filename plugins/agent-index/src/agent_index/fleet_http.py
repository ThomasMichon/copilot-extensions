"""Initial-header deadlines around Uvicorn's maintained HTTP parser."""

import asyncio

from uvicorn.protocols.http.h11_impl import H11Protocol

INITIAL_REQUEST_TIMEOUT = 5


class FleetHTTPProtocol(H11Protocol):
    def connection_made(self, transport: asyncio.Transport) -> None:
        self._header_deadline: asyncio.TimerHandle | None = None
        super().connection_made(transport)
        self._arm_header_deadline()

    def _cancel_header_deadline(self) -> None:
        if self._header_deadline is not None:
            self._header_deadline.cancel()
            self._header_deadline = None

    def _arm_header_deadline(self) -> None:
        self._cancel_header_deadline()
        self._header_deadline = self.loop.call_later(INITIAL_REQUEST_TIMEOUT, self.transport.abort)

    def handle_events(self) -> None:
        super().handle_events()
        if self.cycle is not None and not self.cycle.response_complete:
            self._cancel_header_deadline()

    def on_response_complete(self) -> None:
        self._arm_header_deadline()
        super().on_response_complete()

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_header_deadline()
        super().connection_lost(exc)

    def shutdown(self) -> None:
        self._cancel_header_deadline()
        super().shutdown()
