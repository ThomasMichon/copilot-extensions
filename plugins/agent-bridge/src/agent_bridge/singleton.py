"""Single-instance guard -- at most one agent-bridge daemon per config dir.

Thin adapter over the shared ``single_instance_lease`` primitive (extracted from
this module so bridge/vault/index/worktrees inherit one correct implementation --
copilot-extensions #737). The mechanism -- an OS-level, exclusive, non-blocking
byte-range lock the kernel frees automatically when the holder dies, so a stale
lock can never wedge startup -- now lives in the library; this module only pins
the agent-bridge naming so the on-disk lock and every reader are unchanged.

A second ``agent-bridge start`` against the same config dir + port must *refuse*
instead of spawning a duplicate daemon (the root cause behind the installer's
flaky restart -- see #129). Keying on the **config dir** (not the plugin/venv
folder) is deliberate: the primary daemon (``~/.agent-bridge``) and the elevated
sub-daemon (``~/.agent-bridge/elevated``) have distinct config dirs, so each is
allowed its own single instance while two *primaries* can never coexist. Keying
additionally on the **port** lets an active and a passive daemon coexist on one
config dir during a zero-downtime cutover.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from single_instance_lease import AlreadyRunningError, read_owner_pid
from single_instance_lease import SingleInstance as _SingleInstance

if TYPE_CHECKING:
    import os

log = logging.getLogger("agent-bridge")

# Historical service name -> lock filenames ``agent-bridge.lock`` /
# ``agent-bridge.<port>.lock``. Unchanged across the extraction so a running
# daemon's lock and every ``_read_holder_pid`` reader keep working.
_SERVICE = "agent-bridge"

# Preserve the historical private name imported by ``__main__`` and the tests.
_read_holder_pid = read_owner_pid

__all__ = ["AlreadyRunningError", "SingleInstance", "_read_holder_pid"]


class SingleInstance(_SingleInstance):
    """agent-bridge's config-dir-scoped daemon singleton.

    Delegates to :class:`single_instance_lease.SingleInstance` with
    ``service="agent-bridge"`` so the lock filenames and log channel are
    unchanged. The instance MUST stay referenced while the daemon runs -- if it
    is garbage collected the underlying handle closes and the OS lock releases.
    """

    def __init__(
        self,
        config_dir: str | os.PathLike[str],
        port: int | None = None,
    ) -> None:
        super().__init__(config_dir, service=_SERVICE, port=port, logger=log)
