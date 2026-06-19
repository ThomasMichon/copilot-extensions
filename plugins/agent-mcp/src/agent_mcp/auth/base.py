"""Auth injector protocol and base classes.

An :class:`AuthInjector` knows how to apply credentials to an outgoing request,
in whichever way the transport needs them:

* HTTP transport calls :meth:`headers` and merges the result into request headers.
* stdio transport calls :meth:`child_env` and merges the result into the wrapped
  child process environment.

On an upstream auth failure (e.g. HTTP 401) the bridge calls :meth:`invalidate`
and retries once, so cached tokens can be refreshed.
"""

from __future__ import annotations

import abc

from ..config import AuthSpec


class AuthInjector:
    """Applies credentials to outgoing requests for one bridge.

    Plain base with no-op defaults; subclasses override only what they need.
    """

    name: str = "auth"

    async def headers(self) -> dict[str, str]:
        """Headers to add to an HTTP upstream request (empty if none)."""
        return {}

    async def child_env(self) -> dict[str, str]:
        """Environment overrides for a stdio child process (empty if none)."""
        return {}

    async def invalidate(self) -> None:
        """Drop any cached credential so the next call re-acquires."""
        return None


class NoneInjector(AuthInjector):
    """Passthrough -- inject nothing."""

    name = "none"


class CompositeInjector(AuthInjector):
    """Merge several injectors -- e.g. inject two secrets into two env vars.

    :meth:`headers` and :meth:`child_env` union the results of each wrapped
    injector in order, so later entries win on a key collision. :meth:`invalidate`
    fans out to every wrapped injector (so a 401 retry refreshes them all).
    """

    name = "composite"

    def __init__(self, injectors: list[AuthInjector]) -> None:
        self.injectors = injectors

    async def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for inj in self.injectors:
            out.update(await inj.headers())
        return out

    async def child_env(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for inj in self.injectors:
            out.update(await inj.child_env())
        return out

    async def invalidate(self) -> None:
        for inj in self.injectors:
            await inj.invalidate()


class TokenInjector(AuthInjector, abc.ABC):
    """Base for single-bearer-token injectors.

    Subclasses implement :meth:`_acquire` to fetch a token string. Header
    placement (name + value template) and the stdio target env var come from the
    :class:`AuthSpec`. Tokens are cached until :meth:`invalidate`.
    """

    def __init__(self, spec: AuthSpec) -> None:
        self.spec = spec
        self._cached: str | None = None

    @abc.abstractmethod
    async def _acquire(self) -> str | None:
        """Fetch a fresh token (or None on failure)."""

    async def _get(self) -> str | None:
        if self._cached is None:
            self._cached = await self._acquire()
        return self._cached

    async def invalidate(self) -> None:
        self._cached = None

    async def headers(self) -> dict[str, str]:
        token = await self._get()
        if not token:
            return {}
        return {self.spec.header: self.spec.format.format(token=token)}

    async def child_env(self) -> dict[str, str]:
        token = await self._get()
        if not token or not self.spec.target_env:
            return {}
        return {self.spec.target_env: token}
