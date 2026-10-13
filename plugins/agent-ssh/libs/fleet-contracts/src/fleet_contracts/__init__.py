"""Portable data contracts; parsing never enrolls, connects or authorizes a target."""

from .records import (
    ConnectorRegistration,
    ContractError,
    CredentialParameters,
    CredentialResult,
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
from .responses import BackendResponseError, IndexHealth, IndexHit, IndexSearchResult

__all__ = [
    "ConnectorRegistration",
    "ContractError",
    "CredentialParameters",
    "CredentialResult",
    "DriverSnapshot",
    "DriverTarget",
    "HealthResult",
    "RouteRequest",
    "RouteResponse",
    "SearchHit",
    "SearchParameters",
    "ServiceOffer",
    "TargetRef",
    "BackendResponseError",
    "IndexHealth",
    "IndexHit",
    "IndexSearchResult",
    "decode_json",
    "encode_json",
]
