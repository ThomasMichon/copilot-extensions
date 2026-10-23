"""Negotiated drain and rollback controls for ``BridgeClient``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from .client_worktree_restart import _SupportsRequest


class _SupportsDrain(_SupportsRequest, Protocol):
    def health(self) -> dict[str, Any]: ...


if TYPE_CHECKING:
    _Base = _SupportsDrain
else:
    _Base = object


class DrainClientMixin(_Base):
    """Mixin supplying default cold and strict graceful drain controls."""

    def drain(
        self,
        *,
        timeout: float = 300.0,
        poll: float = 1.0,
        force: bool = False,
        source: str | None = None,
        reason: str | None = None,
        on_incompatible: str = "cold",
        contract_minimum: int = 1,
        contract_maximum: int = 1,
    ) -> dict[str, Any]:
        """POST /api/v1/drain -- stop accepting new work and wait for in-flight
        sessions to settle (the zero-downtime pre-swap step).

        If this process is a descendant of a Copilot session (its
        ``AGENT_BRIDGE_SESSION_ID`` env is set -- e.g. an agent running an
        in-session ``test-chamber services agent-bridge update``), that session
        is passed as ``exclude_session_id`` so the redeploy's graceful-cancel
        does not cancel the very turn driving the update (#1790).
        """
        import os as _os

        from pydantic import ValidationError
        from .client import BridgeClientError
        from .drain_contract import DrainContract, DrainRequest
        from .protocol import NEGOTIATED_DRAIN_PROTOCOL_VERSION

        caller = DrainContract(minimum=contract_minimum, maximum=contract_maximum)
        control = DrainRequest(
            timeout=timeout, poll=poll, force=force, on_incompatible=on_incompatible,
            drain_contract=caller,
        )
        body: dict[str, Any] = control.model_dump(exclude_none=True)
        health = self.health()
        if not isinstance(health, dict):
            raise BridgeClientError(409, "drain negotiation is unavailable; no mismatch established")
        version = health.get("protocol_version")
        negotiated = type(version) is int and version >= NEGOTIATED_DRAIN_PROTOCOL_VERSION
        peer = None
        compatible = False
        if negotiated:
            try:
                peer = DrainContract.model_validate(health.get("drain_contract"))
            except ValidationError as exc:
                raise BridgeClientError(409, "invalid drain negotiation; no mismatch established") from exc
            compatible = caller.overlaps(peer)
            if on_incompatible == "refuse" and not compatible:
                raise BridgeClientError(409, "incompatible drain contract; graceful-only control refused")
        else:
            if not caller.compatible:
                raise BridgeClientError(409, "daemon does not support negotiated drain; mismatch is unknown")
            # Unknown/legacy negotiation cannot authorize cold effects. Preserve
            # the ordinary drain path without sending a speculative range.
            body.pop("drain_contract")
            body.pop("on_incompatible")
        if source:
            body["source"] = source
        if reason:
            body["reason"] = reason
        self_sid = _os.environ.get("AGENT_BRIDGE_SESSION_ID")
        if self_sid:
            body["exclude_session_id"] = self_sid
        result = self._request(
            "POST", "/api/v1/drain",
            body=body,
            request_timeout=timeout + 30.0,
        )
        if not isinstance(result, dict):
            raise BridgeClientError(502, "drain outcome must be an object")
        if peer is not None:
            if any(type(result.get(name)) is not bool for name in ("drained", "clean", "forced")):
                raise BridgeClientError(
                    502, "negotiated drain outcome was not confirmed: completion flags must be booleans",
                )
            receiver = result.get("drain_contract")
            prior_cold_refusal = (
                result.get("drained") is False and result.get("clean") is False
                and result.get("forced") is True and result.get("mode") == "incompatible-contract-cold"
            )
            if (
                type(receiver) is not int or not peer.minimum <= receiver <= peer.maximum
                or result.get("contract_status") != ("compatible" if compatible else "incompatible")
                or compatible and not caller.minimum <= receiver <= caller.maximum
                or not prior_cold_refusal and result.get("mode") != (
                    "graceful" if compatible else "incompatible-contract-cold"
                )
            ):
                raise BridgeClientError(502, "negotiated drain outcome was not confirmed")
        else:
            if result.get("mode") == "incompatible-contract-cold":
                raise BridgeClientError(502, "unnegotiated drain cannot confirm cold effects")
            result["contract_status"] = "legacy-unnegotiated"
        return result

    def drain_gracefully(
        self, *, timeout: float = 300.0, poll: float = 1.0, force: bool = False,
        source: str | None = None, reason: str | None = None,
        contract_minimum: int = 1, contract_maximum: int = 1,
    ) -> dict[str, Any]:
        """Strict cutover control: mismatch refuses before any drain POST."""
        from .client import BridgeClientError

        if force:
            raise BridgeClientError(409, "graceful-only drain cannot force")
        return self.drain(
            timeout=timeout, poll=poll, force=False, source=source, reason=reason,
            contract_minimum=contract_minimum, contract_maximum=contract_maximum,
            on_incompatible="refuse",
        )

    def undrain(self) -> dict[str, Any]:
        """POST /api/v1/undrain -- release the drain gate (rollback)."""
        return self._request("POST", "/api/v1/undrain") or {}
