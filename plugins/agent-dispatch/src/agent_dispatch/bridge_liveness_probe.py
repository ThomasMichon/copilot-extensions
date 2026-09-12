"""A real HOT/WARM/COLD live-probe for the bridge/session state machine.

Phase 10 item 4 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-10-live-wiring.md``):
wire :func:`agent_dispatch.bridge_state_machine.resolve_liveness`/
:func:`~agent_dispatch.bridge_state_machine.resolve_resume` against a real
agent-bridge liveness read.

**This item does not depend on the agent-bridge-ahp-convergence effort.**
That effort is about exposing an *external* Agent Host Protocol surface --
an unrelated concern. The liveness read this item needs already exists and
is already in production use: :func:`agent_dispatch.embody.local_body_verdict`
/ :func:`~agent_dispatch.embody.fleet_body_verdict` (both built on
``agent-bridge --json status <session_id>``) are exactly what item 5's
spawn-consistency sweep already calls. The only reason this module exists
separately from those is a **finer distinction than they need**: those
helpers collapse agent-bridge's actual session status down to a tri-state
LIVE/GONE/UNKNOWN (enough to answer "is this body still around"), but
:class:`agent_dispatch.bridge_state_machine.Liveness` needs the sharper
HOT/WARM/COLD split -- specifically, HOT (an active turn is genuinely in
flight right now -- attaching a second controller now would race it) versus
WARM (alive, idle, safe to reattach). This module reads agent-bridge's own
``SessionStatus`` values (``plugins/agent-bridge/src/agent_bridge/
models.py``) directly rather than through the coarser tri-state, because
that distinction already exists in agent-bridge's real session model:

- ``running`` -> :attr:`~Liveness.HOT`: agent-bridge's own model means an
  agent turn is actively executing right now.
- ``idle``, ``created``, ``starting`` -> :attr:`~Liveness.WARM`: alive, no
  turn in flight -- safe to reattach without racing an active driver.
- ``stopping``, ``stopped``, ``failed``, ``ended`` -> :attr:`~Liveness.COLD`.
- Not-found, a transport/parse failure, or any status value this module
  does not recognize -> :attr:`~Liveness.HOT`, never guessed as WARM or
  COLD. This is the deliberately conservative default when the read is
  ambiguous: refusing an unnecessary resume (the ``HOT`` outcome, absent
  ``force_takeover``) is always safe, where wrongly resolving WARM or COLD
  risks a double-attached controller or an orphaned duplicate spawn.

Only the local-body probe (this machine's own agent-bridge daemon) is
implemented here. A fleet (SSH) variant would follow the same status-value
mapping against :func:`agent_dispatch.embody.fleet_body_verdict`'s
underlying probe; left for a follow-up slice, not blocked on anything.

Wiring this probe into a real ``resume``/reconciliation call site is a
**separate follow-up slice**, not done here: every plausible call site
(``supervisor.py``, ``embody.py``) is already at its grandfathered
module-size ceiling (`tools/module-size-baseline.json`), so adding a call
there means either splitting one of those modules first or a deliberate,
reviewed widening -- a decision this slice does not make unilaterally.
"""

from __future__ import annotations

import json
import subprocess

from .bridge import _agent_bridge_launch_prefix
from .bridge_state_machine import Liveness
from .procutil import no_window_kwargs

#: agent-bridge's own ``SessionStatus`` values (``plugins/agent-bridge/src/
#: agent_bridge/models.py``), mapped onto the coarser three-tier ``Liveness``
#: this machine declares. Values absent from every set below fall through to
#: the conservative ``HOT`` default -- see the module docstring.
_HOT_STATUSES = frozenset({"running"})
_WARM_STATUSES = frozenset({"idle", "created", "starting"})
_COLD_STATUSES = frozenset({"stopping", "stopped", "failed", "ended"})


def local_body_liveness_probe(session_id: str, *, timeout: float | None = None) -> Liveness:
    """Resolve HOT/WARM/COLD for a local headless body via ``agent-bridge
    --json status <session_id>`` (no SSH -- this machine's own daemon).

    Never raises: every ambiguous or failing read resolves to
    :attr:`Liveness.HOT` (see the module docstring for why that is the safe
    default here, unlike the ``UNKNOWN`` tri-state
    :func:`agent_dispatch.embody.local_body_verdict` uses for the same
    underlying read).
    """
    if not session_id:
        return Liveness.HOT
    exe = _agent_bridge_launch_prefix()
    if exe is None:
        return Liveness.HOT
    cmd = [*exe, "--json", "status", session_id]
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved above
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else 8.0,
            **no_window_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return Liveness.HOT
    if proc.returncode != 0:
        err = (proc.stderr or "") + (proc.stdout or "")
        return Liveness.COLD if "not found" in err.lower() else Liveness.HOT
    out = (proc.stdout or "").strip()
    if not out:
        return Liveness.HOT
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return Liveness.HOT
    if not isinstance(data, dict):
        return Liveness.HOT
    status = str(data.get("status") or "").strip().lower()
    if status in _HOT_STATUSES:
        return Liveness.HOT
    if status in _WARM_STATUSES:
        return Liveness.WARM
    if status in _COLD_STATUSES:
        return Liveness.COLD
    return Liveness.HOT
