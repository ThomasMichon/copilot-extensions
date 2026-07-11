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

Extensions are discovered two ways (union, deduped), each pointing at a
``register(registry)`` callable:

1. Python entry points in the ``agent_vault.extensions`` group.
2. The ``AGENT_VAULT_EXTENSIONS`` env var -- comma-separated ``module`` or
   ``module:callable`` paths.

Loading is idempotent and fail-open: a broken extension is logged and skipped,
never crashing the daemon or CLI.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("agent-vault.extensions")

ENTRY_POINT_GROUP = "agent_vault.extensions"
EXTENSIONS_ENV = "AGENT_VAULT_EXTENSIONS"

# Hook callable aliases (documentation only -- hooks are duck-typed).
UnlockProvider = Callable[["UnlockContext"], "str | None"]
ProtocolAction = Callable[[Any, dict, "ActionContext"], dict]
ClientTransport = Callable[[dict, "float | None", "TransportContext"], "dict | None"]
ConfigSource = Callable[["str | None"], dict]


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


@dataclass(order=True)
class _Ranked:
    priority: int
    seq: int
    name: str = field(compare=False)
    fn: Callable = field(compare=False)


class ExtensionRegistry:
    """Holds registered hooks and exposes ordered access to them."""

    def __init__(self) -> None:
        self._unlock_providers: list[_Ranked] = []
        self._actions: dict[str, Callable] = {}
        self._transports: list[_Ranked] = []
        self._config_sources: list[_Ranked] = []
        self._seq = 0
        self._loaded = False

    # -- registration ----------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def register_unlock_provider(
        self, fn: UnlockProvider, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a provider consulted before the interactive unlock prompt."""
        self._unlock_providers.append(
            _Ranked(priority, self._next_seq(), name or getattr(fn, "__name__", "?"), fn)
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
        self, fn: ClientTransport, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a client transport consulted after the built-in transports."""
        self._transports.append(
            _Ranked(priority, self._next_seq(), name or getattr(fn, "__name__", "?"), fn)
        )
        self._transports.sort()

    def register_config_source(
        self, fn: ConfigSource, *, priority: int = 100, name: str | None = None
    ) -> None:
        """Register a configuration source feeding the resolver."""
        self._config_sources.append(
            _Ranked(priority, self._next_seq(), name or getattr(fn, "__name__", "?"), fn)
        )
        self._config_sources.sort()

    # -- ordered access --------------------------------------------------

    @property
    def unlock_providers(self) -> list[_Ranked]:
        return list(self._unlock_providers)

    @property
    def actions(self) -> dict[str, Callable]:
        return dict(self._actions)

    def action(self, name: str) -> Callable | None:
        return self._actions.get(name)

    @property
    def transports(self) -> list[_Ranked]:
        return list(self._transports)

    @property
    def config_sources(self) -> list[_Ranked]:
        return list(self._config_sources)

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
                pw = ranked.fn(ctx)
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
        self, request: dict, timeout: float | None, ctx: TransportContext
    ) -> dict | None:
        """Consult client transports in priority order; first non-None wins."""
        for ranked in self._transports:
            try:
                result = ranked.fn(request, timeout, ctx)
            except Exception as exc:
                log.warning("Transport %r raised: %s", ranked.name, exc)
                continue
            if result is not None:
                result.setdefault("_transport", f"ext:{ranked.name}")
                return result
        return None

    def collect_config(self, cwd: str | None) -> dict[str, Any]:
        """Merge config-source contributions.

        Sources run in priority order; a lower-priority source only fills keys a
        higher-priority one did not set. Returns a flat mapping.
        """
        merged: dict[str, Any] = {}
        for ranked in self._config_sources:
            try:
                contribution = ranked.fn(cwd)
            except Exception as exc:
                log.warning("Config source %r raised: %s", ranked.name, exc)
                continue
            if not isinstance(contribution, dict):
                continue
            for key, value in contribution.items():
                merged.setdefault(key, value)
        return merged


# ---------------------------------------------------------------------------
# Discovery / loading
# ---------------------------------------------------------------------------


def _resolve_register(target: str) -> Callable | None:
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
    return fn


def _env_targets() -> list[str]:
    raw = os.environ.get(EXTENSIONS_ENV, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _entry_point_registers() -> list[Callable]:
    try:
        from importlib.metadata import entry_points
    except Exception:
        return []
    try:
        eps = entry_points()
        # Python 3.10+: selectable API; older shape is a dict.
        selected = (
            eps.select(group=ENTRY_POINT_GROUP)
            if hasattr(eps, "select")
            else eps.get(ENTRY_POINT_GROUP, [])
        )
    except Exception as exc:
        log.warning("Entry-point discovery failed: %s", exc)
        return []
    registers: list[Callable] = []
    for ep in selected:
        try:
            fn = ep.load()
        except Exception as exc:
            log.warning("Could not load extension entry point %r: %s", ep.name, exc)
            continue
        if callable(fn):
            registers.append(fn)
    return registers


def load_extensions(registry: ExtensionRegistry) -> ExtensionRegistry:
    """Discover and register all extensions into ``registry`` (idempotent)."""
    if registry._loaded:
        return registry
    registry._loaded = True

    registers: list[Callable] = list(_entry_point_registers())
    for target in _env_targets():
        fn = _resolve_register(target)
        if fn is not None:
            registers.append(fn)

    for register in registers:
        try:
            register(registry)
        except Exception as exc:
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
