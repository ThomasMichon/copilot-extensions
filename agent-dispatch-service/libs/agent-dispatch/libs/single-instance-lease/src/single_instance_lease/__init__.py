"""single_instance_lease -- one live daemon per service per host.

Service-neutral primitives that make "at most one active daemon owns a service
on a host" an *asserted, repairable* property, extracted from agent-bridge so any
Copilot CLI plugin or multi-machine service reuses one implementation:

- ``lease`` -- :class:`SingleInstance`: an OS-level, liveness-reconciled lease a
  daemon acquires before it becomes the active endpoint. A process that cannot
  acquire it stands down instead of racing an incumbent. The kernel frees the
  lock when the holder dies, so a dead owner's lease is immediately reclaimable
  and a stale lock never wedges startup.
- ``supersession`` -- :func:`is_superseded`: the pure, fail-safe decision a
  demoted daemon uses to self-retire once a live, strictly-newer generation has
  taken over (operates on a plain routing-table ``dict``; no routing-lib
  dependency).
- ``reaper`` -- :func:`reconcile_set_reap`: the outside backstop that retires a
  service's identified strays down to the single ``active`` daemon, fail-soft and
  never touching ``active`` or ``self``.

Realizes the ``single-instance-lease`` behavior of the plugin-services vision.
The library carries no service-specific logic; consumers inject their own
process-identity, terminate, and table-read collaborators.
"""

from .lease import (
    AlreadyRunningError,
    SingleInstance,
    read_owner_pid,
)
from .reaper import (
    ReapResult,
    reconcile_set_reap,
    superseded_pids_from_table,
)
from .supersession import (
    is_listening,
    is_superseded,
    pid_alive,
)

__all__ = [
    "AlreadyRunningError",
    "ReapResult",
    "SingleInstance",
    "is_listening",
    "is_superseded",
    "pid_alive",
    "read_owner_pid",
    "reconcile_set_reap",
    "superseded_pids_from_table",
]
