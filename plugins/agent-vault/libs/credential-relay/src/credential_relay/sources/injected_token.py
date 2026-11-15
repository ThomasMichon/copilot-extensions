"""Injected Azure-token source (test/relay-shim affordance).

Serves the ``get-azure-token`` action from a **pre-minted bearer supplied in an
environment variable**, instead of shelling ``az account get-access-token`` like
:class:`~credential_relay.sources.az_login.AzLoginSource`.

Why this exists
---------------
The relay mints Azure/ADO REST bearers on the **relay host** via
``AzLoginSource`` -> the host's ``az`` identity. In a hermetic test venue (e.g.
the clean-room container that stands in for the host) there is no interactive
``az login``, and a Windows host's MSAL token cache is DPAPI-encrypted and cannot
be mounted into a Linux container. So a live end-to-end test of the relay path
needs a way to feed a **host-minted** bearer (the operator mints it on the real
host with their own ``az`` and passes it in) that the in-venue relay serves
without itself running ``az``.

Safety / production-inertness
-----------------------------
This source is a no-op unless its env var is set: :meth:`resolve` returns
``None`` when the variable is empty/unset, so the relay's routing (first source
whose ``resolve`` returns non-``None`` wins) **falls through to the real
``AzLoginSource``**. It is only ever wired *ahead of* ``AzLoginSource`` when
Azure minting is already enabled, and it is reached only *after* the server's
token gate authorizes the ``get-azure-token`` request -- it does not widen who
may mint, only *where the bearer comes from* once authorized. It never logs the
token. Intended for testing the relay end-to-end, not for production credential
service.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os

log = logging.getLogger("credential-relay.injected-token")

# Default env var the source reads the pre-minted bearer from. A downstream test
# harness mints the bearer on the host and forwards this var into the venue
# (e.g. the clean-room runner's --pass-env), so the in-venue relay serves it.
DEFAULT_ENV_VAR = "CREDENTIAL_RELAY_INJECTED_AZURE_TOKEN"


def _jwt_exp(token: str) -> int | None:
    """Best-effort ``exp`` (epoch seconds) from a JWT bearer, else ``None``.

    Used only to populate ``expires_on`` so downstream caches age the token
    correctly; a non-JWT or unparseable token simply omits the field.
    """
    try:
        payload_b64 = token.split(".", 2)[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if exp is not None else None
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError, TypeError):
        return None


class InjectedTokenSource:
    """Serves ``get-azure-token`` from a pre-minted bearer in an env var.

    Passthrough (returns ``None``) when the env var is unset, so it is inert in
    production and hands off to the next source (``AzLoginSource``). Honors the
    same exact-match resource allowlist as ``AzLoginSource`` (``"*"`` = any).
    """

    def __init__(
        self,
        allowed_resources: list[str] | None = None,
        *,
        env_var: str = DEFAULT_ENV_VAR,
    ) -> None:
        self._allowed_resources = frozenset(allowed_resources or [])
        self._env_var = env_var

    @property
    def name(self) -> str:
        return "injected-azure-token"

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        """Supports ``get-azure-token`` only (mirrors AzLoginSource)."""
        return action == "get-azure-token"

    @staticmethod
    def _normalize(target: str) -> str:
        t = target.strip()
        if t.endswith("/.default"):
            t = t[: -len("/.default")]
        return t.rstrip("/")

    def _is_allowed(self, target: str) -> bool:
        if "*" in self._allowed_resources:
            return True
        norm = self._normalize(target)
        return any(self._normalize(a) == norm for a in self._allowed_resources)

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 30.0,
    ) -> str | None:
        """Return the injected bearer in key=value format, or ``None`` to pass on."""
        token = os.environ.get(self._env_var, "").strip()
        if not token:
            # Inert: no injected token -> fall through to the real az-login source.
            return None

        scope = fields.get("scope", "")
        resource = fields.get("resource", "")
        target = scope or resource
        if not target:
            log.warning("get-azure-token request missing 'scope'/'resource' field")
            return None
        if not self._is_allowed(target):
            log.warning(
                "Denied injected get-azure-token for '%s' (not in allowed_resources: %s)",
                target, sorted(self._allowed_resources),
            )
            return None

        host = target.rstrip("/").split("//", 1)[-1] if "//" in target else target
        parts = ["protocol=https", f"host={host}", f"token={token}"]
        exp = _jwt_exp(token)
        if exp is not None:
            parts.append(f"expires_on={exp}")
        log.info(
            "Served INJECTED Azure token for target=%s from $%s (test relay shim)",
            target, self._env_var,
        )
        return "\n".join(parts) + "\n\n"
