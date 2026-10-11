"""Portable data contracts; parsing never enrolls, connects or authorizes a target."""

from .records import (
    ConnectorRegistration,
    ContractError,
    DriverSnapshot,
    DriverTarget,
    HealthResult,
    RouteRequest,
    RouteResponse,
    SearchHit,
    SearchParameters,
    ServiceOffer,
    TargetRef,
    decode_json,
    encode_json,
)

__all__ = [
    "ConnectorRegistration",
    "ContractError",
    "DriverSnapshot",
    "DriverTarget",
    "HealthResult",
    "RouteRequest",
    "RouteResponse",
    "SearchHit",
    "SearchParameters",
    "ServiceOffer",
    "TargetRef",
    "decode_json",
    "encode_json",
]
