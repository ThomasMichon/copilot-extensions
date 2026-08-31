"""Windows Proactor accept-loop resilience for CPython issue #93821."""

from __future__ import annotations

import asyncio
import logging
import sys

log = logging.getLogger("agent-bridge")

_TRANSIENT_ACCEPT_WINERRORS = frozenset({64, 995})


def _is_transient_accept_error(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in _TRANSIENT_ACCEPT_WINERRORS


if sys.platform == "win32":
    import _overlapped
    import socket
    import struct

    from asyncio import exceptions, tasks, trsock, windows_events

    class ResilientIocpProactor(windows_events.IocpProactor):
        """Close failed accept sockets and classify client resets as retryable."""

        def accept(self, listener):
            self._register_with_iocp(listener)
            conn = self._get_accept_socket(listener.family)
            ov = _overlapped.Overlapped(windows_events.NULL)
            ov.AcceptEx(listener.fileno(), conn.fileno())

            def finish_accept(trans, key, ov):
                try:
                    ov.getresult()
                except OSError as exc:
                    if _is_transient_accept_error(exc):
                        conn.close()
                        raise ConnectionResetError(*exc.args) from exc
                    raise
                buf = struct.pack("@P", listener.fileno())
                conn.setsockopt(
                    socket.SOL_SOCKET,
                    _overlapped.SO_UPDATE_ACCEPT_CONTEXT,
                    buf,
                )
                conn.settimeout(listener.gettimeout())
                return conn, conn.getpeername()

            async def accept_coro(future, conn):
                try:
                    await future
                except exceptions.CancelledError:
                    conn.close()
                    raise
                except ConnectionResetError:
                    return

            future = self._register(ov, listener, finish_accept)
            tasks.ensure_future(
                accept_coro(future, conn), loop=self._loop
            )
            return future

    class ResilientProactorEventLoop(asyncio.ProactorEventLoop):
        """Keep a listener open after transient AcceptEx client resets."""

        def __init__(self):
            super().__init__(proactor=ResilientIocpProactor())

        def _start_serving(
            self,
            protocol_factory,
            sock,
            sslcontext=None,
            server=None,
            backlog=100,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None,
        ):
            def accept_loop(f=None):
                try:
                    if f is not None:
                        conn, addr = f.result()
                        if self._debug:
                            log.debug(
                                "%r got a new connection from %r: %r",
                                server,
                                addr,
                                conn,
                            )
                        protocol = protocol_factory()
                        if sslcontext is not None:
                            self._make_ssl_transport(
                                conn,
                                protocol,
                                sslcontext,
                                server_side=True,
                                extra={"peername": addr},
                                server=server,
                                ssl_handshake_timeout=ssl_handshake_timeout,
                                ssl_shutdown_timeout=ssl_shutdown_timeout,
                            )
                        else:
                            self._make_socket_transport(
                                conn,
                                protocol,
                                extra={"peername": addr},
                                server=server,
                            )
                    if self.is_closed():
                        return
                    f = self._proactor.accept(sock)
                except ConnectionResetError:
                    if not self.is_closed() and sock.fileno() != -1:
                        self.call_soon(accept_loop)
                        log.warning(
                            "Transient Windows accept failure ignored; "
                            "listener remains active"
                        )
                    else:
                        sock.close()
                except OSError as exc:
                    if sock.fileno() != -1:
                        self.call_exception_handler(
                            {
                                "message": "Accept failed on a socket",
                                "exception": exc,
                                "socket": trsock.TransportSocket(sock),
                            }
                        )
                        sock.close()
                    elif self._debug:
                        log.debug(
                            "Accept failed on socket %r", sock, exc_info=True
                        )
                except exceptions.CancelledError:
                    sock.close()
                else:
                    self._accept_futures[sock.fileno()] = f
                    f.add_done_callback(accept_loop)

            self.call_soon(accept_loop)


    def resilient_loop_factory():
        """Build the loop Uvicorn must run instead of its default Proactor."""
        return ResilientProactorEventLoop()
