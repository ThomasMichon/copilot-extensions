"""Shared retained streams; backend selection never depends on keeper currency."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hmac
import json
import logging
import time
from pathlib import Path

from agent_procutil.daemon_instances import daemon_entrypoint

from . import execution_context, ipc, sockio
from .config import load_config
from .custody_connector import BackendConnector
from .custody_contract import CONTRACT, supports
from .custody_intake import ClientIntake
from .custody_session import CustodySession
from .custody_wire import MAX_FRAME
from .request_context import CONTEXT_VERSION, request_root
from .serve import Server, _join_cleanup

log = logging.getLogger("agent-mcp.custody")
_RETRY_SECONDS = 30


@dataclass(eq=False)
class HostedSession:
    owner: CustodySession
    connector: BackendConnector
    writer: asyncio.StreamWriter
    generation: str | None = None
    signature: tuple | None = None
    migration: asyncio.Task | None = None
    retry_at: float = 0
    closing: bool = False


class CustodyHost(Server):
    """One installation's shared custody provider, not a current-code service.

    The installation and backend handle must come from qualified adoption, not
    from a client's request. No ordinary CLI or installer selects this class
    yet. Only actually equivalent stdio contexts are eligible.
    """

    def __init__(self, socket_path, backend_handle, runtime_root, **options):
        root = Path(runtime_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("custody requires an existing absolute installation root")
        socket_path = sockio.absolute_handle(socket_path)
        backend_handle = sockio.absolute_handle(backend_handle)
        if Path(socket_path).parent.resolve() == Path(backend_handle).parent.resolve():
            raise ValueError("custody and backend require separate publication namespaces")
        super().__init__(socket_path, lease_service="agent-mcp-custody", **options)
        self.installation = root.resolve()
        self.backend_handle = backend_handle
        # This process preserves connections. Its old physical generation must
        # neither veto current backend admission nor publish a central route.
        self._runtime_root = None
        self.sessions: set[HostedSession] = set()

    async def _start_control_listener(self):
        return None

    def _authenticated(self, request):
        token = request.get("token")
        return self._token is None or (
            isinstance(token, str) and hmac.compare_digest(token, self._token)
        )

    async def _dispatch(self, request):
        if not isinstance(request, dict) or not self._authenticated(request):
            return {"ok": False, "error": "unauthorized"}
        self._touch()
        if request.get("op") == "ping":
            return {
                "ok": True, "pong": True, "custody_host": CONTRACT,
                "configuration_context": CONTEXT_VERSION,
                "execution_context": execution_context.descriptor(),
                "installation_root": str(self.installation),
                "attached": self._attached, "draining": self._draining,
                "ready": self._server is not None and self._server.is_serving(),
            }
        return {"ok": False, "error": "custody supports attach and ping only"}

    def _configuration(self, request):
        if (
            not supports(request.get("custody"))
            or request.get("installation_root") != str(self.installation)
            or not isinstance(request.get("bridge"), str) or not request["bridge"]
            or not isinstance(request.get("execution_context"), dict)
        ):
            raise execution_context.SharingUnavailable("custody adoption identity is required")
        return execution_context.load_config(
            request["bridge"], request, load_config, repository_root=request_root(request),
        )

    def _selection(self):
        selected = self.installation / "current-version"
        with selected.open("r", encoding="utf-8-sig") as stream:
            generation = stream.read(1025).strip()
        if not generation or len(generation) > 1024:
            raise ValueError("backend selection is invalid")
        handle = Path(self.backend_handle)
        path = handle if ipc._HAS_AF_UNIX else ipc._endpoint_path(handle)
        try:
            info = path.lstat()
            endpoint = (info.st_ino, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            endpoint = None
        return generation, endpoint

    async def _migrate(self, entry):
        try:
            await entry.owner.handover(entry.connector)
            entry.generation = entry.connector.generation
        except Exception:
            entry.retry_at = time.monotonic() + _RETRY_SECONDS
            log.warning("custody backend handover incomplete; no replay")
            if entry.owner.state.uncertain:
                entry.writer.close()

    def inspect_sessions(self):
        """Schedule independent handovers; never wait for one client's mutation."""
        for entry in tuple(self.sessions):
            if entry.owner.state.uncertain:
                entry.writer.close()
        try:
            signature = self._selection()
        except (OSError, ValueError):
            return
        for entry in tuple(self.sessions):
            if entry.owner.state.uncertain:
                continue
            if entry.closing or (entry.migration is not None and not entry.migration.done()):
                continue
            if signature[0] == entry.generation:
                continue
            if signature == entry.signature and time.monotonic() < entry.retry_at:
                continue
            entry.signature = signature
            entry.migration = asyncio.create_task(self._migrate(entry))

    async def _sweep_loop(self):
        try:
            while True:
                await asyncio.sleep(1)
                self.inspect_sessions()
                self._maybe_idle_evict()
        except asyncio.CancelledError:
            pass

    async def _settle(self, entry):
        entry.closing = True
        if entry.migration is not None:
            await asyncio.gather(entry.migration, return_exceptions=True)
        try:
            await entry.owner.close()
        except Exception:
            log.warning("custody client teardown incomplete; accepted effects may be unknown")

    async def _run_session(self, request, reader, writer):
        if not self._authenticated(request):
            await self._send(writer, {"ok": False, "error": "unauthorized"})
            return
        try:
            cfg = self._configuration(request)
            connector = BackendConnector(
                self.backend_handle, request["bridge"], self.installation, cfg,
            )
        except Exception as exc:
            await self._send(writer, execution_context.config_failure(exc))
            return

        write_lock = asyncio.Lock()
        attached = False

        async def sink(message):
            wire = (json.dumps(message, allow_nan=False) + "\n").encode()
            if len(wire) > MAX_FRAME:
                raise ValueError("client output frame exceeds its byte budget")
            async with write_lock:
                writer.write(wire)
                await writer.drain()

        async def ready():
            nonlocal attached
            await self._send(writer, {"ok": True, "attached": True, "custody": CONTRACT})
            attached = True

        owner = CustodySession(cfg, sink)
        entry = HostedSession(owner, connector, writer)
        self._attached += 1
        self._touch()
        try:
            await owner.start(connector, ready=ready)
            entry.generation = connector.generation
            self.sessions.add(entry)
            await ClientIntake(owner, reader).run()
        except Exception as exc:
            if not attached:
                await self._send(writer, execution_context.config_failure(exc))
            else:
                log.warning("custody client stream ended without replay")
        finally:
            owner.client_eof()
            try:
                await _join_cleanup(asyncio.create_task(self._settle(entry)))
            finally:
                self.sessions.discard(entry)
                self._attached -= 1
                self._touch()


def main(argv=None):
    """Explicit internal provider entrypoint; does not alter current selection."""
    import argparse

    parser = argparse.ArgumentParser(description="Shared MCP connection custody")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args(argv)
    return _run_host(
        sockio.absolute_handle(args.socket), sockio.absolute_handle(args.backend),
        args.runtime_root,
    )


@daemon_entrypoint(
    "agent-mcp-custody", source=__file__, purpose="production",
    runtime_root=lambda socket_path, backend_handle, runtime_root: str(Path(socket_path).parent),
)
def _run_host(socket_path, backend_handle, runtime_root):
    from . import __version__

    server = CustodyHost(socket_path, backend_handle, runtime_root, version=__version__)
    asyncio.run(server.serve_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
