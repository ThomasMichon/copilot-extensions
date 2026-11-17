"""`PersistentCarrier`: one reconnecting framed stdio carrier.

Composes the three behavioral mixins that previously lived together as
one large class:

- `_CarrierLifecycleMixin` (`carrier_lifecycle.py`) -- connect, reconnect
  backoff, idle retirement, `close()`/`diagnostics()`.
- `_CarrierRequestMixin` (`carrier_requests.py`) -- correlated requests
  and subscription registration/restoration.
- `_CarrierTransportMixin` (`carrier_transport.py`) -- the reader/writer
  loops, heartbeat/staleness monitoring, and envelope dispatch.

All three mixins share the same instance state (process, queues,
pending requests, subscriptions) and call each other's methods through
``self``, which resolves through the instance's MRO regardless of which
mixin file defines the target method -- no explicit wiring is needed
here beyond the base-class list. `_CarrierLifecycleMixin` is listed
first because it owns `__init__`.
"""

from __future__ import annotations

from .carrier_lifecycle import _CarrierLifecycleMixin
from .carrier_requests import _CarrierRequestMixin
from .carrier_transport import _CarrierTransportMixin


class PersistentCarrier(
    _CarrierLifecycleMixin,
    _CarrierRequestMixin,
    _CarrierTransportMixin,
):
    """One reconnecting framed stdio carrier for an SSH connection identity."""
