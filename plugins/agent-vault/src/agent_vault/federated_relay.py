"""Federated account-source channel owned by the existing Vault service loop."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path

from . import federated_relay_config as config
from .config import home_dir

LOG = logging.getLogger(__name__)


def selected_relay():
    selected = config.load(home_dir())
    return RelayRuntime(selected) if selected is not None else None


class RelayRuntime:
    def __init__(self, selected: dict) -> None:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from credential_relay.account_relay import AccountRelay
            from fleet_manager.gateway_account_source import AccountSourceGrant, AccountSourceHandler
            from fleet_manager.gateway_controller_services import ControllerService
            from fleet_manager.gateway_service_channel import origin
            from fleet_manager.gateway_service_connector import verified_tls
        except ImportError:
            raise ValueError("federated-relay-profile-unavailable") from None
        self._selected = selected
        self._origin = origin(selected["gateway_origin"])
        if self._origin != selected["gateway_origin"]:
            raise ValueError("relay-gateway-origin-not-canonical")
        self._key = serialization.load_pem_private_key(
            config.read_reference(Path(selected["proof_key_file"]), 16384), password=None)
        if not isinstance(self._key, ec.EllipticCurvePrivateKey) or not isinstance(
            self._key.curve, ec.SECP256R1,
        ):
            raise ValueError("relay-proof-key-invalid")
        verified_tls(selected["gateway_ca"])
        self._source = AccountRelay(selected["account_policy_file"])
        self._handler = AccountSourceHandler(
            self._source, fleet=selected["fleet"], gateway=selected["gateway"],
            slot=selected["slot"], installation=selected["installation"],
            grants=tuple(AccountSourceGrant(**grant) for grant in selected["grants"]),
        ).binding()
        self._services = (ControllerService(selected["service"], selected["prefix"]),)
        self._access = None
        auth = selected["auth"]
        if auth["kind"] == "az-login":
            from credential_relay.sources.az_login import AzLoginSource
            self._access = AzLoginSource(allowed_resources=[auth["resource"]])
        self._connector = None
        self._task: asyncio.Task | None = None
        self._generation = None
        self._route = None
        self._failures = 0
        self._retry_at = 0.0
        self._attached_at = None
        self._observation = None
        self._observation_deadline = 0.0
        self._closing = False
        self._status = {"state": "waiting-conduit", "channel_attached": False}

    def status(self) -> dict:
        return dict(self._status)

    async def _access_token(self) -> str:
        auth = self._selected["auth"]
        raw = await self._access.resolve(
            "get-azure-token", {"resource": auth["resource"], "tenant": auth["tenant"]},
            timeout=5,
        )
        values = {}
        if isinstance(raw, str) and len(raw) <= 16384:
            for line in raw.strip().splitlines():
                key, separator, value = line.partition("=")
                if not separator or key in values:
                    raise ValueError("relay-admission-token-unavailable")
                values[key] = value
        token = values.get("token")
        if not isinstance(token, str) or not token or len(token) > 8192:
            raise ValueError("relay-admission-token-unavailable")
        return token

    def _conduit(self):
        from fleet_manager.gateway_connect_route import LoopbackRoute
        value = config.document(Path(self._selected["conduit_file"]))
        fields = {
            "schema", "version", "gateway_origin", "connect_host", "connect_port",
            "generation", "state", "expires_at",
        }
        now = time.time()
        if (set(value) != fields or value["schema"] != "fleet-manager.gateway-conduit"
                or type(value["version"]) is not int or value["version"] != 2
                or value["gateway_origin"] != self._origin or value["state"] != "ready"
                or type(value["expires_at"]) not in (int, float)
                or not math.isfinite(value["expires_at"])
                or not now < value["expires_at"] <= now + 10):
            raise ValueError("relay-conduit-unavailable")
        observed = (value["generation"], value["connect_host"], value["connect_port"],
                    value["expires_at"])
        monotonic = time.monotonic()
        if observed != self._observation:
            self._observation = observed
            self._observation_deadline = monotonic + min(9, value["expires_at"] - now)
        if monotonic >= self._observation_deadline:
            raise ValueError("relay-conduit-unavailable")
        generation = config.text(value["generation"])
        return generation, LoopbackRoute(value["connect_host"], value["connect_port"])

    def _connect(self, route):
        from fleet_manager.gateway_service_connector import ServiceConnector
        auth, selected = self._selected["auth"], self._selected
        identity = (
            {"machine_principal": auth["principal"], "machine_audience": auth["audience"]}
            if auth["kind"] == "machine" else {"access_token_callback": self._access_token}
        )
        self._connector = ServiceConnector(
            slot=selected["slot"], gateway_origin=self._origin, gateway_route=route,
            gateway_ca=selected["gateway_ca"], proof_key=self._key, handler=self._handler,
            services=self._services, **identity)
        self._task = asyncio.create_task(self._connector.connect_once())

    async def _disconnect(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._connector = None
        self._attached_at = None

    async def pause(self) -> None:
        await self._disconnect()
        self._status = {"state": "waiting-generation", "channel_attached": False}

    async def tick(self, *, serving: bool = True) -> None:
        if self._closing:
            return
        if not serving:
            await self.pause()
            return
        try:
            generation, route = self._conduit()
        except (OSError, ValueError, TypeError, RecursionError):
            await self._disconnect()
            self._status = {"state": "waiting-conduit", "channel_attached": False}
            return
        if (generation, route) != (self._generation, self._route):
            await self._disconnect()
            self._generation, self._route = generation, route
            self._failures, self._retry_at = 0, 0.0
        now = time.monotonic()
        if self._task is not None and self._task.done():
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
            if self._attached_at is not None and now - self._attached_at >= 60:
                self._failures = 0
            self._attached_at = None
            self._failures += 1
            self._retry_at = now + min(4, 0.5 * 2 ** (self._failures - 1))
            LOG.warning("federated-relay channel-unavailable")
        if self._failures >= 5:
            self._status = {"state": "reconnect-exhausted", "channel_attached": False}
            return
        if self._task is None and now >= self._retry_at:
            self._connect(route)
        attached = self._task is not None and self._connector.connected.is_set()
        if attached and self._attached_at is None:
            self._attached_at = now
        self._status = {
            "state": "attached" if attached else "connecting",
            "channel_attached": attached,
        }

    async def close(self) -> None:
        self._closing = True
        self._status = {"state": "stopped", "channel_attached": False}
        await self._disconnect()
