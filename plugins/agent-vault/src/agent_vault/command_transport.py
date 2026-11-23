"""Transport orchestration with explicit native-boundary dependencies."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import ResolvedVault
    from .extensions import ClientTransport, ExtensionRegistry, TransportContext
    from .rendezvous import Endpoint
    from .transport_binding import MutationBinding

Reply = dict[str, Any] | None
Sender = Callable[[dict[str, Any], float | None], Reply]
_UNLOCK_SENDER: ContextVar[Sender | None] = ContextVar("vault_unlock_sender", default=None)


class ReadSender(Protocol):
    def __call__(
        self, request: dict[str, Any], timeout: float | None, *,
        strict_endpoint: bool, require_endpoint: bool,
    ) -> Reply: ...


_READ_SENDER: ContextVar[ReadSender | None] = ContextVar("vault_read_sender", default=None)


@contextmanager
def use_read_sender(sender: ReadSender) -> Iterator[None]:
    token = _READ_SENDER.set(sender)
    try:
        yield
    finally:
        _READ_SENDER.reset(token)


@contextmanager
def use_unlock_sender(sender: Sender) -> Iterator[None]:
    """Keep an explicit unlock's prompt paths on its already selected route."""
    token = _UNLOCK_SENDER.set(sender)
    try:
        yield
    finally:
        _UNLOCK_SENDER.reset(token)


@dataclass
class CommandRoute:
    """One command's qualified sender, not a failover policy."""

    sender: Sender | None = None


@dataclass(frozen=True)
class TransportBoundary:
    resolve_context: Callable[[], ResolvedVault]
    discover: Callable[..., Endpoint | None]
    socket_send: Callable[..., Reply]
    tcp_send: Callable[[dict[str, Any], str, int, float | None], Reply]
    pipe_send: Callable[[str, dict[str, Any], float | None], Reply]
    registry: Callable[[], ExtensionRegistry]
    transport_context: Callable[..., TransportContext]
    is_windows: bool
    is_wsl: bool
    host: str
    unavailable: type[RuntimeError]
    invalid: type[RuntimeError]


def send_command(
    boundary: TransportBoundary,
    request: dict[str, Any],
    timeout: float | None = 5.0,
    *,
    strict_endpoint: bool = False,
    require_endpoint: bool = False,
    binding: MutationBinding | None = None,
    _context: ResolvedVault | None = None,
    _route: CommandRoute | None = None,
) -> Reply:
    if binding is None and _route is None and _context is None and request.get("action") in {
        "ping", "get", "has",
    }:
        read_sender = _READ_SENDER.get()
        if read_sender is not None:
            return read_sender(
                request, timeout, strict_endpoint=strict_endpoint, require_endpoint=require_endpoint,
            )
    if binding is None and _route is None and _context is None and request.get("action") == "unlock":
        selected_sender = _UNLOCK_SENDER.get()
        if selected_sender is not None:
            return selected_sender(request, timeout)
    if binding is not None:
        binding.require_unselected()
        if _route is not None:
            raise ValueError("Mutation binding cannot share a maintenance route")
        if request.get("action") != "ping" or request.get("health_only") is not True:
            raise ValueError("Transport binding requires a read-only health request")
    if _route is not None and _route.sender is not None:
        return _route.sender(request, timeout)
    context = _context if _context is not None else boundary.resolve_context()
    request = dict(request)

    def tag(result: Reply, transport: str | None) -> Reply:
        if result is not None and transport is not None:
            result["_transport"] = transport
        return result

    host, port = boundary.host, context.port
    discovery_error: RuntimeError | None = None
    try:
        discovered = boundary.discover(context, strict=strict_endpoint)
    except (boundary.unavailable, boundary.invalid) as error:
        if not strict_endpoint:
            raise
        discovered, discovery_error = None, error
    discovered_unix: str | None = None
    discovered_pipe: str | None = None
    if discovered is not None:
        if boundary.is_wsl:
            # A host pipe/socket is not dialable from the guest; use its advertised TCP alternate.
            usable = discovered.usable(lambda transport: transport == "tcp")
            if usable is not None:
                discovered = usable
        if discovered.transport == "unix":
            discovered_unix = discovered.address
        elif discovered.transport == "pipe":
            discovered_pipe = discovered.address
        elif discovered.transport == "tcp":
            try:
                discovered_host, port = discovered.tcp_host_port
                if discovered.source != "windows":
                    host = discovered_host
            except ValueError:
                if strict_endpoint:
                    raise
                discovered = None

    def project(raw: dict[str, Any]) -> dict[str, Any]:
        selected = dict(raw)
        # Host-owned selection must not receive database paths from the guest.
        if not (discovered is not None and discovered.source == "windows"):
            if context.kpdb is not None or raw.get("action") != "cache-maintenance":
                selected.setdefault("kpdb", context.kpdb)
            selected.setdefault("group", context.group)
            selected.setdefault("vault", context.vault_name)
        return selected

    def remember(sender: Sender, transport: str | None, result: Reply) -> None:
        if _route is None or result is None:
            return

        # A lost reply is uncertain; never re-enter transport selection.
        def retained(raw: dict[str, Any], wait: float | None) -> Reply:
            try:
                return tag(sender(project(raw), wait), transport)
            except Exception:
                return None

        _route.sender = retained

    def native(sender: Sender, transport: str) -> Reply:
        result = sender(request, timeout)
        if binding is not None and result is not None:
            binding.select(
                sender, single_attempt=True,
                defaults={
                    key: request[key] for key in ("kpdb", "group", "vault")
                    if key in request
                },
            )
        remember(sender, transport, result)
        return tag(result, transport)

    request = project(request)
    extension_context = boundary.transport_context(
        kpdb=context.kpdb, group=context.group, vault_name=context.vault_name, port=port,
    )

    def extensions(before_builtin: bool) -> Reply:
        registry = boundary.registry()
        if binding is not None:
            return registry.try_transports(
                request, timeout, extension_context, before_builtin=before_builtin, binding=binding,
            )
        if _route is None:
            if before_builtin:
                return registry.try_transports(request, timeout, extension_context, before_builtin=True)
            return registry.try_transports(request, timeout, extension_context)
        selected: list[ClientTransport] = []
        result = registry.try_transports(
            request, timeout, extension_context,
            before_builtin=before_builtin, on_selected=selected.append,
        )
        if selected and result is not None:
            transport = selected[0]
            label = result.get("_transport")
            remember(
                lambda raw, wait: transport(raw, wait, extension_context),
                label if isinstance(label, str) else None, result,
            )
        return result

    result = extensions(True)
    if result is not None:
        return result

    if discovery_error is not None:
        raise discovery_error
    if require_endpoint and discovered is None:
        raise boundary.unavailable("No selected native vault endpoint is advertised")
    if strict_endpoint and discovered is not None and discovered.transport not in (
        "tcp", "pipe" if boundary.is_windows else "unix",
    ):
        return None

    if discovered_pipe is not None and boundary.is_windows:
        result = native(
            lambda raw, wait: boundary.pipe_send(discovered_pipe, raw, wait), "discovered-pipe",
        )
        if result is not None or strict_endpoint:
            return result
    if discovered_unix is not None and not boundary.is_windows:
        result = native(
            lambda raw, wait: boundary.socket_send(raw, wait, path=discovered_unix), "discovered-unix",
        )
        if result is not None or strict_endpoint:
            return result
    if (
        discovered_unix is None and not boundary.is_windows
        and context.sources.get("port") == "default"
        and not (strict_endpoint and discovered is not None)
    ):
        result = native(lambda raw, wait: boundary.socket_send(raw, wait), "unix-socket")
        if result is not None:
            return result
    transport = "discovered-tcp" if discovered is not None and discovered.transport == "tcp" else "tcp"
    result = native(lambda raw, wait: boundary.tcp_send(raw, host, port, wait), transport)
    if result is not None or strict_endpoint and discovered is not None:
        return result
    return extensions(False)
