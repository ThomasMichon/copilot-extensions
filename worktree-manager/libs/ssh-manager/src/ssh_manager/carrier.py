"""Compatibility facade for the persistent SSH stdio carrier split.

The carrier used to live entirely in this one module; it has since been
split by behavioral responsibility into:

- `carrier_protocol.py` -- envelope framing, encode/decode, handshake
  validation, and the carrier error hierarchy.
- `carrier_subscriptions.py` -- bounded queue/budget primitives plus
  `CarrierSubscription` and `CarrierLease`.
- `carrier_lifecycle.py`, `carrier_transport.py`, `carrier_requests.py`
  -- the three mixins composed into `PersistentCarrier`
  (`carrier_persistent.py`).
- `carrier_server.py` -- `StdioCarrierServer`, the remote-side endpoint.

This module re-exports the same public names so existing
``from ssh_manager.carrier import ...`` call sites keep working
unchanged; new code should prefer importing directly from the
module that owns the name.
"""

from __future__ import annotations

from .carrier_persistent import PersistentCarrier
from .carrier_protocol import (
    DEFAULT_MAX_BUFFERED_BYTES,
    DEFAULT_MAX_FRAME_SIZE,
    DEFAULT_MAX_QUEUED_FRAMES,
    MIN_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    CarrierBackpressure,
    CarrierError,
    CarrierProtocolError,
    CarrierRemoteError,
    CarrierStale,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
    decode_envelope,
    encode_envelope,
    hello_envelope,
    negotiated_frame_size,
    read_envelope,
    read_envelope_sync,
    validate_hello,
    write_envelope_sync,
)
from .carrier_server import RequestHandler, StdioCarrierServer
from .carrier_subscriptions import CarrierLease, CarrierSubscription

__all__ = [
    "DEFAULT_MAX_BUFFERED_BYTES",
    "DEFAULT_MAX_FRAME_SIZE",
    "DEFAULT_MAX_QUEUED_FRAMES",
    "MIN_PROTOCOL_VERSION",
    "PROTOCOL_VERSION",
    "CarrierBackpressure",
    "CarrierError",
    "CarrierLease",
    "CarrierProtocolError",
    "CarrierRemoteError",
    "CarrierStale",
    "CarrierSubscription",
    "CarrierUnavailable",
    "Envelope",
    "EnvelopeType",
    "PersistentCarrier",
    "RequestHandler",
    "StdioCarrierServer",
    "decode_envelope",
    "encode_envelope",
    "hello_envelope",
    "negotiated_frame_size",
    "read_envelope",
    "read_envelope_sync",
    "validate_hello",
    "write_envelope_sync",
]
