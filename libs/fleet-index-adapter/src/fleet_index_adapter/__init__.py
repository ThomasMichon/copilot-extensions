"""fleet-index-adapter: the routing-foundation service adapter for agent-index.

See README.md for the full contract. ``route()`` is the only entry point this
package exposes; everything else is an implementation detail.
"""

from __future__ import annotations

from .adapter import BackendUnavailable, route

__all__ = ["BackendUnavailable", "route"]
