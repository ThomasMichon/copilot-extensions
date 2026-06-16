"""Credential relay TCP server.

Listens on a configurable port (default 9857) for git-credential-protocol
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
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .sources import CredentialSource

log = logging.getLogger("agent-codespaces.relay")

DEFAULT_PORT = 9857


def _addr_in_use(exc: OSError) -> bool:
    """True if an OSError is an 'address already in use' bind failure."""
    import errno

    codes = {errno.EADDRINUSE}
    # Windows WSAEADDRINUSE (10048) is not always mapped to EADDRINUSE.
    codes.add(getattr(errno, "WSAEADDRINUSE", 10048))
    return exc.errno in codes


def _pid_on_port(port: int) -> int | None:
    """Best-effort: find the PID listening on 127.0.0.1:*port* (cross-platform)."""
    import subprocess as sp
    import sys

    if sys.platform == "win32":
        ps = (
            f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -First 1)"
            ".OwningProcess"
        )
        try:
            out = sp.run(["powershell", "-NoProfile", "-Command", ps],  # noqa: S603, S607
                         capture_output=True, text=True, timeout=15)
            val = (out.stdout or "").strip()
            return int(val) if val.isdigit() else None
        except (OSError, sp.TimeoutExpired, ValueError):
            return None
    for cmd in (["ss", "-lptnH", f"sport = :{port}"], ["lsof", "-ti", f"tcp:{port}"]):
        try:
            out = sp.run(cmd, capture_output=True, text=True, timeout=15)  # noqa: S603
        except (OSError, sp.TimeoutExpired):
            continue
        text = out.stdout or ""
        if cmd[0] == "lsof":
            lines = text.strip().splitlines()
            if lines and lines[0].isdigit():
                return int(lines[0])
        else:
            import re

            m = re.search(r"pid=(\d+)", text)
            if m:
                return int(m.group(1))
    return None


def _terminate_pid(pid: int) -> None:
    """Best-effort terminate a local process by pid (cross-platform)."""
    import subprocess as sp
    import sys

    if sys.platform == "win32":
        sp.run(["taskkill", "/PID", str(pid), "/F", "/T"],  # noqa: S603, S607
               capture_output=True, text=True)
    else:
        import signal as _signal

        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass


def _reclaim_port(port: int) -> bool:
    """Evict a stale holder of the dedicated relay *port* and wait for release.

    The relay port is dedicated, so a process holding it after our bind fails is
    an orphaned previous relay (e.g. an ungracefully-killed agent-bridge daemon).
    Terminate it and wait briefly for the OS to release the binding. Returns True
    if the port appears free afterwards. Never evicts the current process.
    """
    pid = _pid_on_port(port)
    if not pid or pid == os.getpid():
        return False
    log.warning(
        "Relay port %d held by stale process pid %d -- evicting to reclaim it",
        port, pid,
    )
    _terminate_pid(pid)
    for _ in range(20):  # up to ~2s for the OS to release the binding
        if _pid_on_port(port) != pid:
            return True
        time.sleep(0.1)
    return _pid_on_port(port) != pid


# Actions the relay recognizes
_KNOWN_ACTIONS = frozenset({
    "get", "store", "erase",
    "fill", "approve", "reject",
    "get-github-token",
    "get-azure-token",
    "get-access-token",
})

# git-credential "get" semantics -- a request asking for a username/password.
# When one of these cannot be resolved we must FAIL FAST (see below) rather
# than return nothing, because an empty response lets git fall through to an
# interactive terminal prompt that blocks indefinitely.
_GET_ACTIONS = frozenset({"get", "fill"})

# Fail-fast sentinel. Per the git-credential protocol, a helper that returns
# ``quit=1`` makes git abort the whole credential-helper chain immediately
# (``fatal: credential helper ... told us to quit``, exit 128) instead of
# prompting. This converts a silent ~52-min ``git credential fill`` hang into a
# prompt, explicit auth failure surfaced to the Copilot caller.
_FAILFAST_RESPONSE = "quit=1\n\n"


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
    failfast_responses: int = 0
    token_rejections: int = 0
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
        ado_host: str | None = None,
        token_validator: Callable[[str], bool] | None = None,
        token_required_actions: frozenset[str] | None = None,
    ) -> None:
        self.port = port
        self.sources = sources or []
        self.policy = policy or RelayPolicy()
        self.stats = RelayStats()
        self._server: asyncio.Server | None = None
        # Optional per-connection shared-secret gate. When set, requests whose
        # action is in ``token_required_actions`` must carry a matching
        # ``auth=<token>`` field (validated by ``token_validator``) or they are
        # denied. Used by container targets reached over host.docker.internal,
        # which -- unlike the SSH-tunnel-isolated codespace path -- are network
        # reachable. Open actions (git get/fill, get-github-token) are never
        # gated, so the codespace relay behavior is unchanged.
        self.token_validator = token_validator
        self.token_required_actions = token_required_actions or frozenset()
        # Default ADO host for bare `get-access-token` requests that carry no
        # host (e.g. npm/nuget via ado-auth-helper). Resolved from the explicit
        # arg or the CODESPACES_ADO_HOST env var; never hardcoded to a specific
        # organization.
        self.ado_host = ado_host or os.environ.get("CODESPACES_ADO_HOST")

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        """Start the TCP relay server, reclaiming the port from a stale holder.

        On a fresh start the bind normally succeeds. If a previous relay (e.g.
        an ungracefully-killed agent-bridge daemon) still holds the dedicated
        relay port, the first bind fails with "address already in use"; we evict
        the stale owner (#19) and retry once so the relay -- and therefore ADO
        auth over the tunnel -- comes up instead of being silently disabled.
        """
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                host="127.0.0.1",
                port=self.port,
            )
        except OSError as exc:
            if not self.port or not _addr_in_use(exc):
                raise
            if not _reclaim_port(self.port):
                raise
            await asyncio.sleep(0.5)
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
                # Fail fast for git `get`/`fill`: a policy-rejected host would
                # otherwise leave git waiting on an interactive prompt.
                await self._maybe_failfast(action, writer, addr, "policy rejected")
                return

            # Token gate: actions that require a shared-secret must present a
            # valid ``auth=<token>``. Never logged. Stripped before routing so
            # it can't leak into a source response or confuse a source.
            token = fields.pop("auth", "")
            if action in self.token_required_actions:
                if not self.token_validator or not self.token_validator(token):
                    log.warning(
                        "[%s] Token gate denied action=%s (missing/invalid token)",
                        addr, action,
                    )
                    self.stats.token_rejections += 1
                    await self._maybe_failfast(action, writer, addr, "token rejected")
                    return

            # Handle get-access-token: synthesize a credential request for ADO
            # and return just the raw token (password). Used by ado-auth-helper
            # and non-git tools (npm, nuget) that need a bare PAT.
            if action == "get-access-token":
                ado_host = fields.get("host") or self.ado_host
                if not ado_host:
                    log.warning(
                        "[%s] get-access-token: no host provided and no "
                        "CODESPACES_ADO_HOST configured", addr,
                    )
                    self.stats.errors += 1
                    return
                ado_fields = {
                    "protocol": "https",
                    "host": ado_host,
                }
                response = await self._route_to_source("get", ado_fields)
                if response:
                    token = ""
                    for line in response.strip().split("\n"):
                        if line.startswith("password="):
                            token = line[len("password="):]
                            break
                    if token:
                        writer.write((token + "\n\n").encode("utf-8"))
                        await writer.drain()
                        log.info("[%s] get-access-token: token (%d chars)", addr, len(token))
                    else:
                        log.warning("[%s] get-access-token: no password in response", addr)
                        self.stats.errors += 1
                else:
                    log.warning("[%s] get-access-token: no source resolved", addr)
                    self.stats.errors += 1
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
                # Fail fast for git `get`/`fill` so git aborts immediately
                # instead of dropping to an interactive prompt that hangs.
                await self._maybe_failfast(action, writer, addr, "no source resolved")

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

    async def _maybe_failfast(
        self,
        action: str,
        writer: asyncio.StreamWriter,
        addr,
        reason: str,
    ) -> None:
        """Send a ``quit=1`` fail-fast response for unresolved git get requests.

        Only git-credential ``get``/``fill`` actions get the sentinel: those are
        the ones git would otherwise satisfy with an interactive prompt that
        blocks. Non-git actions (``get-access-token`` etc.) handle failure via a
        non-zero exit on the CodeSpace side and need no sentinel.
        """
        if action not in _GET_ACTIONS:
            return
        try:
            writer.write(_FAILFAST_RESPONSE.encode("utf-8"))
            await writer.drain()
            self.stats.failfast_responses += 1
            log.info("[%s] Fail-fast quit=1 sent (%s)", addr, reason)
        except Exception:
            log.debug("[%s] Could not send fail-fast response", addr, exc_info=True)

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
