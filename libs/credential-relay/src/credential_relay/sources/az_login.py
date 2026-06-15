"""Azure CLI token source.

Returns Azure access tokens via ``az account get-access-token``.
Handles the ``get-azure-token`` relay action, returning the token in
git-credential-protocol key=value format for uniform framing.

This is a HIGH-TRUST credential source. It must be explicitly enabled
and configured with an exact-match resource allowlist. Tokens are
bearer credentials that grant cloud control equivalent to the host's
``az login`` session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time

log = logging.getLogger("credential-relay.az-login")

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Safety margin: expire cached tokens 5 minutes early
_EXPIRY_SAFETY_MARGIN = 300


def _az_argv(rest: list[str]) -> list[str] | None:
    """Build an argv that can launch the Azure CLI cross-platform.

    On Windows ``az`` is ``az.cmd``; ``create_subprocess_exec`` cannot launch a
    ``.cmd``/``.bat`` directly (CreateProcess needs ``cmd.exe``), so resolve the
    real path via ``shutil.which`` and route batch wrappers through ``cmd /c``.
    Returns None if the CLI is not found.
    """
    az = shutil.which("az")
    if not az:
        return None
    if sys.platform == "win32" and az.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", az, *rest]
    return [az, *rest]


class AzLoginSource:
    """Resolves Azure access tokens via ``az account get-access-token``.

    Supports the ``get-azure-token`` action only. Returns tokens in
    key=value format::

        protocol=https
        host=management.azure.com
        token=eyJ0eXAi...
        expires_on=1700000000

    Security:
    - Disabled by default -- must be explicitly enabled in codespaces.yaml
    - Exact-match resource allowlist -- no globbing
    - Tokens are cached until 5 minutes before expiry
    - Token values are never logged (only resource/tenant metadata)
    - Requests for unlisted resources are denied with a clear message
    """

    def __init__(
        self,
        allowed_resources: list[str] | None = None,
        cache_ttl_override: float | None = None,
    ) -> None:
        self._allowed_resources = frozenset(allowed_resources or [])
        self._cache_ttl_override = cache_ttl_override
        # {(resource, tenant): (response_text, expiry_time)}
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}

    @property
    def name(self) -> str:
        return "az-login"

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Supports ``get-azure-token`` action only."""
        return action == "get-azure-token"

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 30.0,
    ) -> str | None:
        """Resolve an Azure access token via ``az account get-access-token``."""
        resource = fields.get("resource", "")
        tenant = fields.get("tenant", "")

        # Policy: exact-match resource allowlist
        if not resource:
            log.warning("get-azure-token request missing 'resource' field")
            return None

        if resource not in self._allowed_resources:
            log.warning(
                "Denied get-azure-token for resource '%s' "
                "(not in allowed_resources: %s)",
                resource,
                sorted(self._allowed_resources),
            )
            return None

        # Check cache
        cache_key = (resource, tenant)
        cached = self._cache.get(cache_key)
        if cached is not None:
            response_text, expiry = cached
            if time.time() < expiry:
                log.info(
                    "Cache hit for resource=%s (expires in %ds)",
                    resource,
                    int(expiry - time.time()),
                )
                return response_text
            del self._cache[cache_key]

        # Call az CLI
        result = await self._run_az_get_token(
            resource, tenant=tenant, timeout=timeout,
        )
        if result is None:
            return None

        token_data, response_text = result

        # Cache with TTL from token expiry (minus safety margin)
        expiry_time = self._compute_cache_expiry(token_data)
        if expiry_time > time.time():
            self._cache[cache_key] = (response_text, expiry_time)
            log.info(
                "Cached token for resource=%s tenant=%s (expires in %ds)",
                resource,
                token_data.get("tenant", "default"),
                int(expiry_time - time.time()),
            )

        return response_text

    def _compute_cache_expiry(self, token_data: dict) -> float:
        """Compute cache expiry from Azure CLI token response."""
        if self._cache_ttl_override is not None:
            return time.time() + self._cache_ttl_override

        # Azure CLI returns expiresOn (ISO 8601) or expires_on (epoch)
        expires_on = token_data.get("expires_on") or token_data.get("expiresOn")
        if expires_on is not None:
            try:
                # Try epoch timestamp first
                expiry = float(expires_on)
                return expiry - _EXPIRY_SAFETY_MARGIN
            except (ValueError, TypeError):
                pass

            # Try ISO 8601 datetime
            try:
                from datetime import datetime, timezone

                # Handle both "2024-01-15 12:00:00.000000" and ISO formats
                dt_str = str(expires_on).replace(" ", "T")
                if "+" not in dt_str and "Z" not in dt_str:
                    dt_str += "+00:00"
                dt = datetime.fromisoformat(dt_str)
                return dt.replace(tzinfo=timezone.utc).timestamp() - _EXPIRY_SAFETY_MARGIN
            except (ValueError, TypeError):
                log.warning("Could not parse token expiry: %s", expires_on)

        # Fallback: 1 hour cache
        return time.time() + 3600 - _EXPIRY_SAFETY_MARGIN

    async def _run_az_get_token(
        self,
        resource: str,
        *,
        tenant: str = "",
        timeout: float = 30.0,
    ) -> tuple[dict, str] | None:
        """Run ``az account get-access-token`` and return (parsed_json, response_text)."""
        args = _az_argv([
            "account", "get-access-token",
            "--resource", resource,
            "--output", "json",
            *(["--tenant", tenant] if tenant else []),
        ])
        if args is None:
            log.error("az CLI not found on PATH")
            return None

        log.info(
            "Requesting Azure token for resource=%s tenant=%s",
            resource,
            tenant or "default",
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            log.error(
                "az account get-access-token timed out (%.0fs) for resource=%s",
                timeout,
                resource,
            )
            return None
        except FileNotFoundError:
            log.error("az CLI not found on PATH")
            return None

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            log.error(
                "az account get-access-token failed (exit %d) for resource=%s: %s",
                proc.returncode,
                resource,
                err,
            )
            return None

        raw = stdout.decode(errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("Invalid JSON from az CLI: %s", exc)
            return None

        token = data.get("accessToken", "")
        if not token:
            log.error("az CLI returned empty accessToken for resource=%s", resource)
            return None

        # Build response in key=value format (never log the token)
        host = resource.rstrip("/").split("//", 1)[-1] if "//" in resource else resource
        parts = [
            f"protocol=https",
            f"host={host}",
            f"token={token}",
        ]
        if "tenant" in data:
            parts.append(f"tenant={data['tenant']}")
        expires_on = data.get("expires_on") or data.get("expiresOn")
        if expires_on is not None:
            parts.append(f"expires_on={expires_on}")

        response_text = "\n".join(parts) + "\n\n"
        return data, response_text
