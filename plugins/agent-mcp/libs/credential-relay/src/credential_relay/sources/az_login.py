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

    @staticmethod
    def _normalize(target: str) -> str:
        """Canonicalize a resource/scope for allowlist matching.

        Treats the resource form (``https://storage.azure.com/``) and the scope
        form (``https://storage.azure.com/.default``) as equivalent, ignoring a
        trailing slash, so an allowlist entry in either form matches a request
        in either form.
        """
        t = target.strip()
        if t.endswith("/.default"):
            t = t[: -len("/.default")]
        return t.rstrip("/")

    def _is_allowed(self, target: str) -> bool:
        """Whether a resource/scope may be minted (``*`` = any)."""
        if "*" in self._allowed_resources:
            return True
        norm = self._normalize(target)
        return any(self._normalize(a) == norm for a in self._allowed_resources)

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 30.0,
    ) -> str | None:
        """Resolve an Azure access token via ``az account get-access-token``.

        Accepts either a ``scope`` field (an AAD scope like
        ``https://storage.azure.com/.default``, passed to ``az ... --scope``) or
        a ``resource`` field (``https://storage.azure.com/``, ``--resource``).
        The official ``azure-auth-helper get-access-token "<scope>"`` contract
        sends a scope.
        """
        scope = fields.get("scope", "")
        resource = fields.get("resource", "")
        tenant = fields.get("tenant", "")
        target = scope or resource

        if not target:
            log.warning("get-azure-token request missing 'scope'/'resource' field")
            return None

        # Policy: allowlist (exact, normalized) unless any-scope ("*").
        if not self._is_allowed(target):
            log.warning(
                "Denied get-azure-token for '%s' "
                "(not in allowed_resources: %s)",
                target,
                sorted(self._allowed_resources),
            )
            return None

        # Check cache
        cache_key = (target, tenant)
        cached = self._cache.get(cache_key)
        if cached is not None:
            response_text, expiry = cached
            if time.time() < expiry:
                log.info(
                    "Cache hit for target=%s (expires in %ds)",
                    target,
                    int(expiry - time.time()),
                )
                return response_text
            del self._cache[cache_key]

        # Call az CLI
        result = await self._run_az_get_token(
            resource=resource, scope=scope, tenant=tenant, timeout=timeout,
        )
        if result is None:
            return None

        token_data, response_text = result

        # Cache with TTL from token expiry (minus safety margin)
        expiry_time = self._compute_cache_expiry(token_data)
        if expiry_time > time.time():
            self._cache[cache_key] = (response_text, expiry_time)
            log.info(
                "Cached token for target=%s tenant=%s (expires in %ds)",
                target,
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
        resource: str = "",
        *,
        scope: str = "",
        tenant: str = "",
        timeout: float = 30.0,
    ) -> tuple[dict, str] | None:
        """Run ``az account get-access-token`` and return (parsed_json, response_text).

        Prefers ``--scope`` when a scope is given (the official
        ``azure-auth-helper`` contract sends an AAD scope like
        ``https://storage.azure.com/.default``); otherwise ``--resource``.
        """
        target = scope or resource
        cred_args = ["--scope", scope] if scope else ["--resource", resource]
        args = _az_argv([
            "account", "get-access-token",
            *cred_args,
            "--output", "json",
            *(["--tenant", tenant] if tenant else []),
        ])
        if args is None:
            log.error("az CLI not found on PATH")
            return None

        log.info(
            "Requesting Azure token for target=%s tenant=%s",
            target,
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
                "az account get-access-token timed out (%.0fs) for target=%s",
                timeout,
                target,
            )
            return None
        except FileNotFoundError:
            log.error("az CLI not found on PATH")
            return None

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            log.error(
                "az account get-access-token failed (exit %d) for target=%s: %s",
                proc.returncode,
                target,
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
            log.error("az CLI returned empty accessToken for target=%s", target)
            return None

        # Build response in key=value format (never log the token)
        host = target.rstrip("/").split("//", 1)[-1] if "//" in target else target
        parts = [
            "protocol=https",
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
