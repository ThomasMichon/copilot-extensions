"""Extension seam for agent-vault.

The core daemon and CLI ship a fixed feature set. Downstream harnesses often need
to weave in extra behavior at a few specific points -- an alternate way to obtain
the master password, additional daemon actions, a non-local client transport, or
extra configuration sources. Rather than fork the core, those harnesses register
**extensions** against this seam.

Four generic hook categories are exposed, each a plain callable:

- **unlock-source provider** ``provider(ctx) -> str | None`` -- consulted by the
  daemon *before* the interactive prompt; returns a candidate master password to
  verify, or ``None`` to fall through. (e.g. pull the password from a broker.)
- **protocol action** ``handler(service, request, ctx) -> dict`` -- adds a daemon
  request action keyed by name; consulted *before* the ``Unknown action``
  fallback. (e.g. a ``git-credential`` responder.)
- **client transport** ``transport(request, timeout, ctx) -> dict | None`` --
  consulted by the CLI *after* the built-in unix-socket and TCP transports both
  fail; returns a response dict or ``None`` to fall through. (e.g. reach a daemon
  over a tunnel.)
- **config source** ``source(cwd) -> dict`` -- contributes resolver settings
  (``kpdb``/``group``/``port``/``vault`` and arbitrary keys) at a precedence tier
  below repo config and above the named-vault base. (e.g. a per-machine map.)
- **cache source** ``source(machine) -> iterable`` -- yields entries for
  ``cache-populate`` to pre-warm, each a ``"path"`` string or an
  ``(entry, field)`` pair. (e.g. entries derived from installed services.)
- **CLI command** ``builder(subparsers) -> None`` -- adds one (or more)
  subcommand(s) to the ``agent-vault`` CLI. The builder receives the argparse
  subparsers action and calls ``subparsers.add_parser(...)`` then
  ``set_defaults(func=handler)``, where ``handler(args) -> int | None`` runs the
  command. Consulted *after* the built-in verbs are registered, so an extension
  adds multi-machine system-only verbs instead of forking ``cli.py``.
- **startup hook** ``hook(service, ctx) -> None`` -- run once, in priority order,
  when the daemon begins serving (after its listeners are bound and its endpoint
  is advertised). Lets an extension start a long-lived background task tied to the
  daemon's lifetime -- e.g. a transport bridge or a watcher -- instead of forking
  the service launcher. A hook that raises is logged and skipped (fail-open).

Extensions are discovered two ways (union, deduped), each pointing at a
``register(registry)`` callable:

1. Python entry points in the ``agent_vault.extensions`` group.
2. The ``AGENT_VAULT_EXTENSIONS`` env var -- comma-separated ``module`` or
   ``module:callable`` paths.

Loading is idempotent and fail-open: a broken extension is logged and skipped,
never crashing the daemon or CLI. Loader failures are retained so strict
configuration consumers can refuse incomplete policy instead of falling back.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from .transport_binding import MutationBinding

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

log = logging.getLogger("agent-vault.extensions")

ENTRY_POINT_GROUP = "agent_vault.extensions"
EXTENSIONS_ENV = "AGENT_VAULT_EXTENSIONS"

# Hook callable aliases (documentation only -- hooks are duck-typed).
UnlockProvider = Callable[["UnlockContext"], "str | None"]
ProtocolAction = Callable[[Any, dict[str, Any], "ActionContext"], dict[str, Any]]
ClientTransport = Callable[[dict[str, Any], "float | None", "TransportContext"], "dict[str, Any] | None"]
TransportBinder = Callable[["TransportContext"], "ClientTransport | None"]
ConfigSource = Callable[["str | None"], dict[str, Any]]
CacheSource = Callable[["str | None"], "Iterable[object] | None"]
CliCommand = Callable[[Any], None]
StartupHook = Callable[[Any, "StartupContext"], None]


@dataclass(frozen=True)
class UnlockContext:
    """Context passed to unlock-source providers."""

    kpdb: str
    vault_name: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ActionContext:
    """Context passed to protocol-action handlers."""

    kpdb: str | None
    group: str | None
    vault_name: str
    reason: str


@dataclass(frozen=True)
class TransportContext:
    """Context passed to client transports."""

    kpdb: str | None
    group: str | None
    vault_name: str | None
    port: int


@dataclass(frozen=True)
class StartupContext:
    """Context passed to daemon startup hooks.

    Handed to each hook once, when the daemon begins serving. ``run_dir`` is the
    directory holding the rendezvous (endpoint) file; ``port`` is the *actually
    bound* TCP port (never 0 -- resolved after the listener binds).
    """

    kpdb: str | None
    group: str | None
    vault_name: str
    port: int
    run_dir: str


@dataclass(order=True)
class _Ranked:
    priority: int
    seq: int
    name: str = field(compare=False)
    fn: Callable[..., Any] = field(compare=False)
    single_attempt: bool = field(default=False, compare=False)
    bind: TransportBinder | None = field(default=None, compare=False)


def _hook_name(fn: Callable[..., Any], name: str | None) -> str:
    value = name or getattr(fn, "__name__", "?")
    return value if isinstance(value, str) else "?"


class ExtensionRegistry:
    """Holds registered hooks and exposes ordered access to them."""

    def __init__(self) -> None:
        self._unlock_providers: list[_Ranked] = []
        self._actions: dict[str, ProtocolAction] = {}
        self._transports_before: list[_Ranked] = []
        self._transports_after: list[_Ranked] = []
        self._config_sources: list[_Ranked] = []
        self._cache_sources: list[_Ranked] = []
        self._cli_commands: list[_Ranked] = []
        self._startup_hooks: list[_Ranked] = []
        self._seq = 0
        self._loaded = False
        self._load_failed = False

    # -- registration ----------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def register_unlock_provider(
        self, fn: UnlockProvider, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a provider consulted before the interactive unlock prompt."""
        self._unlock_providers.append(
            _Ranked(priority, self._next_seq(), _hook_name(fn, name), fn)
        )
        self._unlock_providers.sort()

    def register_action(self, name: str, fn: ProtocolAction) -> None:
        """Register a daemon protocol action keyed by ``name``."""
        if not name:
            raise ValueError("action name is required")
        if name in self._actions:
            log.warning("Extension action %r already registered; overriding", name)
        self._actions[name] = fn

    def register_transport(
        self, fn: ClientTransport, *, priority: int = 100, name: str | None = None,
        before_builtin: bool = False,
        single_attempt: bool = False,
        bind: TransportBinder | None = None,
    ) -> None:
        """Register a client transport.

        By default a transport is a *fallback*, consulted only after the built-in
        unix-socket + TCP transports both fail. Pass ``before_builtin=True`` to
        register a transport consulted *ahead* of the built-ins -- for a transport
        that must take precedence over the local daemon in some contexts. Such a
        transport should return ``None`` when it does not apply, so the built-ins
        still run.

        ``single_attempt=True`` additionally promises that one callback invocation
        pins one owner/route, sends at most once and never retries or falls through
        internally after a lost reply. Only such callbacks may carry a bound
        create-only mutation; existing callbacks remain usable for ordinary reads.
        A retained multi-request route additionally needs ``bind(ctx)`` to resolve
        and capture its connector before qualification. The returned callable
        must not rediscover another peer. Ordinary one-shot calls keep ``fn``.
        """
        target = self._transports_before if before_builtin else self._transports_after
        target.append(
            _Ranked(
                priority, self._next_seq(), _hook_name(fn, name), fn,
                single_attempt=single_attempt,
                bind=bind,
            )
        )
        target.sort()

    def register_config_source(
        self, fn: ConfigSource, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a configuration source feeding the resolver."""
        self._config_sources.append(
            _Ranked(priority, self._next_seq(), _hook_name(fn, name), fn)
        )
        self._config_sources.sort()

    def register_cache_source(
        self, fn: CacheSource, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register configured targets for population and unlock-triggered warming.

        The callable receives an optional ``machine`` hint and returns an iterable
        of entries to cache -- each either a ``"path"`` string (defaults the field
        to ``password``) or an ``(entry, field)`` pair.
        """
        self._cache_sources.append(
            _Ranked(priority, self._next_seq(), _hook_name(fn, name), fn)
        )
        self._cache_sources.sort()

    def register_cli_command(
        self, fn: CliCommand, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a builder that adds subcommand(s) to the ``agent-vault`` CLI.

        The callable receives the argparse *subparsers action* and should call
        ``subparsers.add_parser(...)`` then ``set_defaults(func=handler)`` where
        ``handler(args) -> int | None`` runs the command. Builders run in priority
        order *after* the built-in verbs, letting a downstream harness add its own
        verbs instead of forking ``cli.py``.
        """
        self._cli_commands.append(
            _Ranked(priority, self._next_seq(), _hook_name(fn, name), fn)
        )
        self._cli_commands.sort()

    def register_startup(
        self, fn: StartupHook, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a hook run once when the daemon begins serving.

        The callable receives the live ``VaultService`` and a
        :class:`StartupContext`. Hooks run in priority order after the daemon's
        listeners are bound and its endpoint advertised, letting a downstream
        harness start a long-lived background task (e.g. a transport bridge)
        without forking the service launcher. A hook that raises is logged and
        skipped (fail-open) so a broken extension never prevents the daemon from
        serving.
        """
        self._startup_hooks.append(
            _Ranked(priority, self._next_seq(), _hook_name(fn, name), fn)
        )
        self._startup_hooks.sort()

    # -- ordered access --------------------------------------------------

    @property
    def unlock_providers(self) -> list[_Ranked]:
        return list(self._unlock_providers)

    @property
    def actions(self) -> dict[str, ProtocolAction]:
        return dict(self._actions)

    def action(self, name: str) -> ProtocolAction | None:
        return self._actions.get(name)

    @property
    def transports(self) -> list[_Ranked]:
        return list(self._transports_before) + list(self._transports_after)

    @property
    def config_sources(self) -> list[_Ranked]:
        return list(self._config_sources)

    @property
    def cache_sources(self) -> list[_Ranked]:
        return list(self._cache_sources)

    @property
    def cli_commands(self) -> list[_Ranked]:
        return list(self._cli_commands)

    @property
    def startup_hooks(self) -> list[_Ranked]:
        return list(self._startup_hooks)

    # -- hook invocation helpers ----------------------------------------

    def provide_unlock(
        self, ctx: UnlockContext, verify: Callable[[str], bool]
    ) -> tuple[str, str] | None:
        """Consult providers in priority order until one yields a valid password.

        ``verify`` decides whether a candidate password is correct. Returns
        ``(password, provider_name)`` for the first provider whose candidate
        verifies, or ``None`` if none do (fail-open to the interactive prompt).
        A provider that raises, yields an empty candidate, or yields a candidate
        that fails verification is skipped in favor of the next provider.
        """
        for ranked in self._unlock_providers:
            try:
                pw = cast(UnlockProvider, ranked.fn)(ctx)
            except Exception as exc:
                log.warning("Unlock provider %r raised: %s", ranked.name, exc)
                continue
            if not pw:
                continue
            try:
                ok = verify(pw)
            except Exception as exc:
                log.warning("Verifying provider %r password raised: %s", ranked.name, exc)
                continue
            if ok:
                log.info("Unlock provider %r supplied a valid password", ranked.name)
                return pw, ranked.name
            log.warning("Unlock provider %r supplied an invalid password", ranked.name)
        return None

    def try_transports(
        self, request: dict[str, Any], timeout: float | None, ctx: TransportContext,
        *, before_builtin: bool = False,
        binding: MutationBinding | None = None,
        on_selected: Callable[[ClientTransport], None] | None = None,
    ) -> dict[str, Any] | None:
        """Consult client transports in priority order; first non-None wins.

        ``before_builtin`` selects the phase: the transports registered to run
        ahead of the built-in unix/TCP transports, or (default) the fallback
        transports consulted after them.
        ``on_selected`` receives the actual winning callable. A command retaining
        that route invokes it directly, not this fail-open selection loop.
        """
        transports = self._transports_before if before_builtin else self._transports_after
        for ranked in transports:
            transport = cast(ClientTransport, ranked.fn)
            try:
                binder = getattr(ranked, "bind", None)
                retained = on_selected is not None or binding is not None
                if retained and binder is not None:
                    selected = binder(ctx)
                    if selected is None:
                        continue
                    transport = selected
                result = transport(request, timeout, ctx)
            except Exception as exc:
                if on_selected is not None:
                    log.warning("Extension transport failed during qualification")
                else:
                    log.warning("Transport %r raised: %s", ranked.name, exc)
                continue
            if result is not None:
                if on_selected is not None and not isinstance(result, dict):
                    return {"ok": False, "error": "invalid_extension_transport_response"}
                if binding is not None:
                    def send(
                        payload: dict[str, Any], limit: float | None,
                        selected: ClientTransport = transport,
                    ) -> dict[str, Any] | None:
                        return selected(payload, limit, ctx)

                    binding.select(
                        send, single_attempt=ranked.single_attempt is True and binder is not None,
                        defaults={
                            key: request[key] for key in ("kpdb", "group", "vault")
                            if key in request
                        },
                    )
                result.setdefault("_transport", f"ext:{ranked.name}")
                if on_selected is not None and binder is not None:
                    on_selected(transport)
                return result
        return None

    def collect_config(self, cwd: str | None, *, strict: bool = False) -> dict[str, Any]:
        """Merge config-source contributions.

        Sources run in priority order; a lower-priority source only fills keys a
        higher-priority one did not set. Strict consumers refuse provider errors
        instead of selecting a fallback policy.
        """
        if strict and self._load_failed:
            raise ValueError("Extension discovery or registration failed")
        merged: dict[str, Any] = {}
        for ranked in self._config_sources:
            try:
                contribution = cast(ConfigSource, ranked.fn)(cwd)
            except Exception as exc:
                if strict:
                    raise ValueError("Configuration source failed") from None
                log.warning("Config source %r raised: %s", ranked.name, exc)
                continue
            if not isinstance(contribution, dict):
                if strict:
                    raise ValueError("Configuration source returned an invalid contribution")
                continue
            for key, value in contribution.items():
                merged.setdefault(key, value)
        return merged

    def collect_cache_entries(self, machine: str | None) -> list[tuple[str, str]]:
        """Collect ``(entry, field)`` pairs to pre-warm from all cache sources.

        Sources run in priority order; the result is deduped preserving first-seen
        order. Each yielded item is either a ``"path"`` string (field defaults to
        ``password``) or an ``(entry, field)`` pair. A source that raises is
        logged and skipped (fail-open).
        """
        from .cache_sources import collect

        return collect(
            (cast(CacheSource, ranked.fn) for ranked in self._cache_sources), machine
        ).entries

    def apply_cli_commands(self, subparsers: Any) -> None:
        """Let each registered CLI-command builder add its subparser(s).

        Builders run in priority order. One that raises is logged and skipped
        (fail-open) so a broken extension never prevents the CLI from starting.
        """
        for ranked in self._cli_commands:
            try:
                ranked.fn(subparsers)
            except Exception as exc:
                log.warning("CLI command builder %r raised: %s", ranked.name, exc)

    def run_startup(self, service: Any, ctx: "StartupContext") -> None:
        """Run each registered startup hook once, in priority order.

        Called by the daemon when it begins serving. A hook that raises is logged
        and skipped (fail-open) so a broken extension never prevents the daemon
        from serving.
        """
        for ranked in self._startup_hooks:
            try:
                ranked.fn(service, ctx)
            except Exception as exc:
                log.warning("Startup hook %r raised: %s", ranked.name, exc)


# ---------------------------------------------------------------------------
# Discovery / loading
# ---------------------------------------------------------------------------


def _resolve_register(target: str) -> Callable[[ExtensionRegistry], None] | None:
    """Resolve a ``module`` or ``module:callable`` string to a register callable."""
    module_name, _, attr = target.partition(":")
    module_name = module_name.strip()
    attr = attr.strip()
    if not module_name:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        log.warning("Could not import extension module %r: %s", module_name, exc)
        return None
    fn = getattr(module, attr or "register", None)
    if not callable(fn):
        log.warning("Extension %r has no callable %r", module_name, attr or "register")
        return None
    return cast(Callable[[ExtensionRegistry], None], fn)


def _env_targets() -> list[str]:
    raw = os.environ.get(EXTENSIONS_ENV, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _entry_point_registers(
    registry: ExtensionRegistry | None = None,
) -> list[Callable[[ExtensionRegistry], None]]:
    try:
        from importlib.metadata import entry_points
    except Exception:
        if registry is not None:
            registry._load_failed = True
        return []
    try:
        eps = entry_points()
        # Python 3.10+: selectable API; older shape is a dict.
        selected = (
            eps.select(group=ENTRY_POINT_GROUP)
            if hasattr(eps, "select")
            else cast("Mapping[str, Iterable[EntryPoint]]", eps).get(ENTRY_POINT_GROUP, [])
        )
    except Exception as exc:
        if registry is not None:
            registry._load_failed = True
        log.warning("Entry-point discovery failed: %s", exc)
        return []
    registers: list[Callable[[ExtensionRegistry], None]] = []
    for ep in selected:
        try:
            fn = ep.load()
        except Exception as exc:
            if registry is not None:
                registry._load_failed = True
            log.warning("Could not load extension entry point %r: %s", ep.name, exc)
            continue
        if callable(fn):
            registers.append(fn)
        elif registry is not None:
            registry._load_failed = True
    return registers


def load_extensions(registry: ExtensionRegistry) -> ExtensionRegistry:
    """Discover and register all extensions into ``registry`` (idempotent)."""
    if registry._loaded:
        return registry
    registry._loaded = True

    registers: list[Callable[[ExtensionRegistry], None]] = list(_entry_point_registers(registry))
    for target in _env_targets():
        fn = _resolve_register(target)
        if fn is not None:
            registers.append(fn)
        else:
            registry._load_failed = True

    for register in registers:
        try:
            register(registry)
        except Exception as exc:
            registry._load_failed = True
            name = getattr(register, "__module__", "?")
            log.warning("Extension register %s failed: %s", name, exc)
    return registry


_REGISTRY: ExtensionRegistry | None = None


def get_registry() -> ExtensionRegistry:
    """Return the process-wide registry, loading extensions on first use."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_extensions(ExtensionRegistry())
    return _REGISTRY


def reset_registry() -> None:
    """Drop the cached registry (test helper)."""
    global _REGISTRY
    _REGISTRY = None
