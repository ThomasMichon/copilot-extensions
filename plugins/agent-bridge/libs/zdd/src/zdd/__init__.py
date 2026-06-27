"""zdd -- zero-downtime active/passive cutover primitives.

A service-neutral library for zero-downtime redeploys, extracted from
agent-bridge so any Copilot CLI plugin or facility service can reuse it:

- ``routing`` -- a file-based client-read routing table (``active.json``):
  publish the live endpoint, atomically flip active/previous on cutover, and
  let short-lived clients resolve (and self-heal to ``previous`` or a config
  fallback) without a long-lived front proxy.
- ``cutover`` -- ``CutoverOrchestrator``: stand a new daemon up beside the old
  on a fresh port, health-gate it, flip the routing table, drain the old, then
  retire it -- with a reversible sequence, rollback, and commit-forward. All
  side-effecting collaborators are injected, so a consumer supplies its own
  spawn / health-probe / HTTP-client / free-port functions and drain semantics.

The library carries no service-specific logic; see ``cutover``'s
``CutoverOrchestrator`` for the consumer contract.
"""

from . import cutover, routing
from .cutover import CutoverError, CutoverOrchestrator, CutoverResult
from .routing import (
    Endpoint,
    clear_if_owner,
    publish_active,
    read_active_endpoint,
    read_table,
    routing_table_path,
)

__all__ = [
    "CutoverError",
    "CutoverOrchestrator",
    "CutoverResult",
    "Endpoint",
    "clear_if_owner",
    "cutover",
    "publish_active",
    "read_active_endpoint",
    "read_table",
    "routing",
    "routing_table_path",
]
