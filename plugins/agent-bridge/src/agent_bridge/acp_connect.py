"""``agent-bridge acp-connect`` -- a transparent stdio <-> ACP-WebSocket relay.

Connects to another agent-bridge's ACP-over-WebSocket endpoint
(``WS /acp/{agent}`` or ``WS /acp/session/{id}``, see ``routes/acp_ws.py``) and
shuttles newline-delimited JSON-RPC frames between that WebSocket and this
process's stdin/stdout. The parent that spawned us (e.g. another bridge's
``acp_client`` driving us as a ``--acp --stdio`` agent) therefore talks to the
*remote* bridge's agent as if it were a local stdio agent.

This is the relay primitive for elevated/federated sessions: an elevated
sub-daemon exposes its agents over ACP-WS, and the primary (non-elevated) bridge
routes an elevated agent to ``acp-connect ws://127.0.0.1:<subport>/acp/<agent>``
as a ``type="command"`` spawn target. No core transport change is needed -- the
primary's ``acp_client`` drives this process over stdio exactly like a local
agent, while we proxy the frames to the remote bridge.

Auth mirrors ``routes/acp_ws.py``: the bearer token is carried both as a
``bearer.<token>`` WebSocket subprotocol (browser-compatible convention) and as
an ``Authorization: Bearer <token>`` header.

Transport is built on **wsproto** (sans-I/O, already an agent-bridge dependency)
over an asyncio stream, deliberately avoiding the ``websockets`` library to keep
the pure-Python / no-native-build guarantee (see pyproject notes).
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import sys
import threading
from urllib.parse import urlsplit

log = logging.getLogger("agent-bridge")

_ACP_SUBPROTOCOL = "acp.v1"
_READ_CHUNK = 65536


def _parse_ws_url(url: str) -> tuple[str, int, str, bool]:
    """Split a ws(s):// URL into (host, port, target_path, tls)."""
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise ValueError(f"acp-connect requires a ws:// or wss:// URL, got {url!r}")
    tls = parts.scheme == "wss"
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if tls else 80)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return host, port, target, tls


async def _relay(url: str, token: str | None) -> int:
    """Connect to the ACP-WS endpoint and relay stdio <-> WebSocket frames."""
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import (
        AcceptConnection,
        BytesMessage,
        CloseConnection,
        Ping,
        RejectConnection,
        Request,
        TextMessage,
    )

    host, port, target, tls = _parse_ws_url(url)
    ssl_ctx: ssl.SSLContext | None = ssl.create_default_context() if tls else None

    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)

    ws = WSConnection(ConnectionType.CLIENT)
    subprotocols = [_ACP_SUBPROTOCOL]
    extra_headers: list[tuple[bytes, bytes]] = []
    if token:
        # acp-ui convention: fold the bearer token into a subprotocol entry,
        # and also send the header for non-browser servers.
        subprotocols.append(f"bearer.{token}")
        extra_headers.append((b"Authorization", f"Bearer {token}".encode()))

    host_header = host if port in (80, 443) else f"{host}:{port}"
    writer.write(
        ws.send(
            Request(
                host=host_header,
                target=target,
                subprotocols=subprotocols,
                extra_headers=extra_headers,
            )
        )
    )
    await writer.drain()

    # --- Complete the handshake before bridging stdio ----------------------
    accepted = False
    while not accepted:
        data = await reader.read(_READ_CHUNK)
        if not data:
            log.error("acp-connect: connection closed during handshake")
            return 1
        ws.receive_data(data)
        for event in ws.events():
            if isinstance(event, AcceptConnection):
                accepted = True
            elif isinstance(event, RejectConnection):
                log.error(
                    "acp-connect: server rejected WebSocket (status=%s) -- "
                    "check the URL and token",
                    event.status_code,
                )
                return 1
        if accepted:
            break

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _stdin_reader() -> None:
        """Blocking thread: stdin NDJSON lines -> out_q (one line per item)."""
        try:
            for raw in iter(sys.stdin.buffer.readline, b""):
                line = raw.strip()
                if line:
                    loop.call_soon_threadsafe(out_q.put_nowait, line)
        finally:
            loop.call_soon_threadsafe(out_q.put_nowait, None)

    threading.Thread(target=_stdin_reader, name="acp-connect-stdin", daemon=True).start()

    stdout = sys.stdout.buffer
    closed = asyncio.Event()

    async def _pump_outbound() -> None:
        """stdin lines -> WebSocket text frames."""
        try:
            while True:
                line = await out_q.get()
                if line is None:  # stdin EOF
                    writer.write(ws.send(CloseConnection(code=1000)))
                    await writer.drain()
                    break
                writer.write(ws.send(TextMessage(line.decode("utf-8", "replace"))))
                await writer.drain()
        except Exception:
            log.debug("acp-connect outbound pump stopped", exc_info=True)
        finally:
            closed.set()

    async def _pump_inbound() -> None:
        """WebSocket frames -> stdout NDJSON; handles ping/close."""
        text_buf: list[str] = []
        try:
            while True:
                data = await reader.read(_READ_CHUNK)
                if not data:
                    break
                ws.receive_data(data)
                for event in ws.events():
                    if isinstance(event, TextMessage):
                        text_buf.append(event.data)
                        if event.message_finished:
                            msg = "".join(text_buf).strip()
                            text_buf = []
                            if msg:
                                stdout.write(msg.encode("utf-8") + b"\n")
                                stdout.flush()
                    elif isinstance(event, BytesMessage):
                        # ACP is text JSON-RPC; ignore unexpected binary frames.
                        if event.message_finished:
                            text_buf = []
                    elif isinstance(event, Ping):
                        writer.write(ws.send(event.response()))
                        await writer.drain()
                    elif isinstance(event, CloseConnection):
                        with __import__("contextlib").suppress(Exception):
                            writer.write(ws.send(event.response()))
                            await writer.drain()
                        return
        except Exception:
            log.debug("acp-connect inbound pump stopped", exc_info=True)
        finally:
            closed.set()

    out_task = asyncio.create_task(_pump_outbound())
    in_task = asyncio.create_task(_pump_inbound())
    await closed.wait()
    for t in (out_task, in_task):
        t.cancel()
    with __import__("contextlib").suppress(Exception):
        writer.close()
        await writer.wait_closed()
    return 0


def cmd_acp_connect(args) -> None:
    """CLI entry: ``agent-bridge acp-connect <ws-url> [--token T] [--stdio]``."""
    token = getattr(args, "token", None)
    if token is None and not getattr(args, "no_token", False):
        # Default to this machine's bridge token (same-host loopback relays).
        from .config import load_or_create_auth_token

        try:
            token = load_or_create_auth_token()
        except Exception:
            token = None

    try:
        rc = asyncio.run(_relay(args.url, token))
    except KeyboardInterrupt:
        rc = 130
    except Exception as exc:  # pragma: no cover - surfaced to the caller
        log.error("acp-connect failed: %s", exc)
        rc = 1
    sys.exit(rc)
