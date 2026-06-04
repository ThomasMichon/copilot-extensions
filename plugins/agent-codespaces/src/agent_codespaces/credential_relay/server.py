"""Credential relay TCP server.

Listens on a configurable port (default 9847) for git-credential-protocol
connections. Parses incoming requests, applies policy checks (allowed
hosts/actions), routes to the first matching credential source, and
returns the response.

Wire protocol::

    <action>\\n          # optional -- defaults to 'get' if omitted
    protocol=https\\n
    host=github.com\\n
    \\n                  # blank line terminates request

The response is git-credential-protocol key=value text terminated by
a blank line.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass, field

from .sources import CredentialSource

log = logging.getLogger("agent-codespaces.relay")

DEFAULT_PORT = 9847

# Actions the relay recognizes
_KNOWN_ACTIONS = frozenset({
    "get", "store", "erase",
    "fill", "approve", "reject",
    "get-github-token",
    "get-azure-token",
})


@dataclass
class RelayPolicy:
    """Policy gate for credential relay requests.

    Controls which actions and hosts are permitted. Requests that
    don't match the policy are rejected before reaching any source.

    Host patterns use fnmatch-style globbing (e.g., ``*.github.com``,
    ``dev.azure.com``). An empty ``allowed_hosts`` list means all
    hosts are allowed (open policy).
    """

    allowed_actions: frozenset[str] = field(
        default_factory=lambda: _KNOWN_ACTIONS,
    )
    allowed_hosts: list[str] = field(default_factory=list)

    def check(self, action: str, fields: dict[str, str]) -> str | None:
        """Return None if allowed, or a rejection reason string."""
        if action not in self.allowed_actions:
            return f"action '{action}' not in allowed list"

        # Host check only applies if allowed_hosts is non-empty
        if self.allowed_hosts:
            host = fields.get("host", "")
            if not any(fnmatch.fnmatch(host, pat) for pat in self.allowed_hosts):
                return f"host '{host}' not in allowed list"

        return None


@dataclass
class RelayStats:
    """Operational statistics for the relay server."""

    total_requests: int = 0
    active_connections: int = 0
    errors: int = 0
    policy_rejections: int = 0
    timeouts: int = 0
    cache_hits: int = 0
    start_time: float | None = None
    last_request_time: float | None = None


class CredentialRelayServer:
    """Async TCP server for git-credential-protocol relay.

    Routes credential requests to pluggable sources (GCM, gh auth, etc.)
    with policy enforcement, stats tracking, and graceful shutdown.

    Usage::

        from agent_codespaces.credential_relay.server import (
            CredentialRelayServer, RelayPolicy,
        )
        from agent_codespaces.credential_relay.sources.git_credential import (
            GitCredentialSource,
        )

        server = CredentialRelayServer(
            sources=[GitCredentialSource()],
            policy=RelayPolicy(allowed_hosts=["github.com", "*.github.com"]),
        )
        await server.start()
        # ... server runs until stopped
        await server.stop()
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        sources: list[CredentialSource] | None = None,
        policy: RelayPolicy | None = None,
    ) -> None:
        self.port = port
        self.sources = sources or []
        self.policy = policy or RelayPolicy()
        self.stats = RelayStats()
        self._server: asyncio.Server | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        """Start the TCP relay server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=self.port,
        )
        self.stats.start_time = time.time()
        log.info(
            "Credential relay started on 127.0.0.1:%d (%d sources, %d allowed hosts)",
            self.port,
            len(self.sources),
            len(self.policy.allowed_hosts),
        )

    async def stop(self) -> None:
        """Stop the relay server gracefully."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            log.info("Credential relay stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single credential relay connection."""
        self.stats.active_connections += 1
        self.stats.total_requests += 1
        self.stats.last_request_time = time.time()

        addr = writer.get_extra_info("peername", ("?", 0))

        try:
            # Read request (key=value lines, terminated by blank line)
            request_text = await self._read_request(reader)
            if not request_text:
                log.warning("[%s] Empty request", addr)
                self.stats.errors += 1
                return

            # Parse action and fields
            action, fields = self._parse_request(request_text)
            log.info(
                "[%s] action=%s host=%s",
                addr, action, fields.get("host", "?"),
            )

            # Policy check
            rejection = self.policy.check(action, fields)
            if rejection:
                log.warning("[%s] Policy rejected: %s", addr, rejection)
                self.stats.policy_rejections += 1
                return

            # Route to source
            response = await self._route_to_source(action, fields)
            if response:
                writer.write(response.encode("utf-8"))
                await writer.drain()
                log.info("[%s] Response sent (%d bytes)", addr, len(response))
            else:
                log.warning("[%s] No source could resolve request", addr)
                self.stats.errors += 1

        except (TimeoutError, asyncio.TimeoutError):
            log.error("[%s] Request timed out", addr)
            self.stats.timeouts += 1
        except Exception:
            log.error("[%s] Error handling request", addr, exc_info=True)
            self.stats.errors += 1
        finally:
            self.stats.active_connections -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_request(
        self, reader: asyncio.StreamReader, timeout: float = 90.0,
    ) -> str:
        """Read a git-credential-protocol request from the stream.

        Reads until a blank line (``\\n\\n``) or EOF, with timeout.
        """
        data = b""
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError("Read timed out")
                chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
                if not chunk:
                    break
                data += chunk
                if b"\n\n" in data or data.endswith(b"\n\n"):
                    break
        except (TimeoutError, asyncio.TimeoutError):
            if data:
                log.warning("Read timed out with partial data (%d bytes)", len(data))
            raise

        return data.decode("utf-8", errors="replace").strip()

    def _parse_request(self, text: str) -> tuple[str, dict[str, str]]:
        """Parse action and fields from request text.

        If the first line contains no ``=``, it is the action.
        Otherwise, the action defaults to ``get``.
        """
        lines = text.split("\n")
        action = "get"
        field_lines = lines

        if lines and lines[0].strip() and "=" not in lines[0]:
            action = lines[0].strip()
            field_lines = lines[1:]

        fields: dict[str, str] = {}
        for line in field_lines:
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip()

        return action, fields

    async def _route_to_source(
        self, action: str, fields: dict[str, str],
    ) -> str | None:
        """Route a credential request to the first matching source."""
        for source in self.sources:
            if source.supports(action, fields):
                log.debug("Routing to source: %s", source.name)
                try:
                    result = await source.resolve(action, fields)
                    if result is not None:
                        return result
                except Exception:
                    log.error(
                        "Source %s failed for action=%s",
                        source.name, action, exc_info=True,
                    )
        return None
