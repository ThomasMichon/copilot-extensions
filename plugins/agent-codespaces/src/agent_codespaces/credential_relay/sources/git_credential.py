"""Git Credential Manager (GCM) proxy source.

Proxies credential requests to the local ``git credential`` command,
which typically resolves through Git Credential Manager. Includes
WSL detection (routes through PowerShell when running under WSL),
credential caching with TTL, and request coalescing for expensive
GCM roundtrips.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time

log = logging.getLogger("agent-codespaces.relay.git-credential")

# Fields that GCM accepts -- newer git sends capability[], wwwauth[],
# etc. that older GCM versions don't understand and may hang on.
_CORE_FIELDS = {"protocol", "host", "username", "password", "path"}

# Action mapping: relay protocol -> git credential subcommand
_ACTION_MAP = {"get": "fill", "store": "approve", "erase": "reject"}

# WSL detection
_IS_WSL = (
    os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
    or "WSL" in os.environ.get("WSL_DISTRO_NAME", "")
)
_POWERSHELL = shutil.which("powershell.exe") if _IS_WSL else None

# Subprocess flags (suppress console windows on Windows)
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class GitCredentialSource:
    """Proxies git-credential requests to local Git Credential Manager.

    Features:
    - WSL: routes through ``powershell.exe`` to reach Windows-side GCM
    - Caching: TTL-based cache for expensive GCM roundtrips (~25s via PS)
    - Coalescing: concurrent requests for the same cache key share one
      GCM invocation
    - Field filtering: strips non-core fields to avoid GCM hangs
    """

    def __init__(self, cache_ttl: float = 300.0) -> None:
        self._cache_ttl = cache_ttl
        # {cache_key: (response_text, expiry_time)}
        self._cache: dict[tuple[str, ...], tuple[str, float]] = {}
        # {cache_key: asyncio.Future} for in-flight request coalescing
        self._inflight: dict[tuple[str, ...], asyncio.Future[str | None]] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "git-credential"

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Supports standard git credential actions (get, store, erase)."""
        return action in ("get", "store", "erase", "fill", "approve", "reject")

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 30.0,
    ) -> str | None:
        """Resolve a git credential request via local GCM."""
        # Normalize action
        git_action = _ACTION_MAP.get(action, action)

        # Build filtered input
        filtered_input = self._filter_fields(fields)

        # Store/erase: execute directly, invalidate cache
        if git_action in ("approve", "reject"):
            result = await self._run_git_credential(
                git_action, filtered_input, timeout=timeout,
            )
            # Invalidate cache for this host
            cache_key = self._cache_key(fields)
            async with self._lock:
                self._cache.pop(cache_key, None)
            return result

        # Fill: check cache, coalesce, call GCM
        cache_key = self._cache_key(fields)
        coalesced_future: asyncio.Future[str | None] | None = None

        # Check cache and in-flight requests under lock
        async with self._lock:
            if cache_key in self._cache:
                cached, expiry = self._cache[cache_key]
                if time.time() < expiry:
                    log.info(
                        "Cache hit for %s (expires in %ds)",
                        fields.get("host", "?"),
                        int(expiry - time.time()),
                    )
                    return cached
                del self._cache[cache_key]

            # Check for in-flight request (coalescing)
            if cache_key in self._inflight:
                coalesced_future = self._inflight[cache_key]
                log.info("Coalescing request for %s", fields.get("host", "?"))

        if coalesced_future is not None:
            return await coalesced_future

        # No cache, no in-flight -- start resolution
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async with self._lock:
            self._inflight[cache_key] = future

        try:
            result = await self._run_git_credential(
                "fill", filtered_input, timeout=max(timeout, 60.0),
            )

            # Cache successful responses
            if result and "password=" in result:
                async with self._lock:
                    self._cache[cache_key] = (result, time.time() + self._cache_ttl)
                    log.info(
                        "Cached credential for %s (TTL: %ds)",
                        fields.get("host", "?"),
                        int(self._cache_ttl),
                    )

            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            return None
        finally:
            async with self._lock:
                self._inflight.pop(cache_key, None)

    def _filter_fields(self, fields: dict[str, str]) -> str:
        """Build git-credential input with only core fields."""
        lines = [
            f"{k}={v}" for k, v in fields.items()
            if k in _CORE_FIELDS
        ]
        return "\n".join(lines) + "\n"

    def _cache_key(self, fields: dict[str, str]) -> tuple[str, str]:
        """Build a cache key from credential fields.

        Uses (protocol, host) only -- username is not included because
        store/erase operations may have different username fields than
        the original fill, and we need invalidation to match.
        """
        return (
            fields.get("protocol", ""),
            fields.get("host", ""),
        )

    async def _run_git_credential(
        self, action: str, credential_input: str, *, timeout: float = 30.0,
    ) -> str | None:
        """Run ``git credential <action>`` as a subprocess."""
        if _IS_WSL and _POWERSHELL and action == "fill":
            return await self._run_via_powershell(credential_input, timeout=timeout)
        return await self._run_directly(action, credential_input, timeout=timeout)

    async def _run_directly(
        self, action: str, credential_input: str, *, timeout: float = 30.0,
    ) -> str | None:
        """Run git credential directly."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "credential", action,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=credential_input.encode()),
                timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("git credential %s timed out (%.0fs)", action, timeout)
            return None
        except FileNotFoundError:
            log.error("git not found on PATH")
            return None

        if proc.returncode != 0:
            log.error(
                "git credential %s failed (exit %d): %s",
                action, proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
            return None

        response = stdout.decode(errors="replace")
        if response and not response.endswith("\n\n"):
            response = response.rstrip("\n") + "\n\n"
        return response

    async def _run_via_powershell(
        self, credential_input: str, *, timeout: float = 60.0,
    ) -> str | None:
        """Run git credential fill via PowerShell (WSL -> Windows GCM)."""
        if not _POWERSHELL:
            log.error("PowerShell not found for WSL credential proxy")
            return None

        # Build PowerShell command with array piping
        lines = [
            line for line in credential_input.strip().split("\n")
            if line.strip()
        ]
        ps_array = ",".join(f"'{line}'" for line in lines) + ",''"
        ps_cmd = f"@({ps_array}) | git credential fill"

        try:
            proc = await asyncio.create_subprocess_exec(
                _POWERSHELL, "-NoProfile", "-Command", ps_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error("PowerShell git credential fill timed out (%.0fs)", timeout)
            return None

        if proc.returncode != 0:
            log.error(
                "PowerShell git credential fill failed (exit %d): %s",
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
            return None

        response = stdout.decode(errors="replace")
        if response and not response.endswith("\n\n"):
            response = response.rstrip("\n") + "\n\n"
        return response
