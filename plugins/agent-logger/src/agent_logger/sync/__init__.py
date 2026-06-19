"""session-sync engine + pluggable targets for agent-logger.

Push raw Copilot session data to a configurable destination:

- ``local`` -- a dotfolder under ``$HOME`` (default).
- ``onedrive`` -- a subfolder under the OneDrive root (NAS-free fleet hub).
- ``ssh`` / ``ssh-tunnel`` -- rsync over SSH (optionally via a jump host).
- ``ingest`` -- an rsync-daemon sink with an optional HTTP notify.

The :mod:`~agent_logger.sync.engine` is transport-blind; transports are
:class:`~agent_logger.sync.targets.base.Target` subclasses.
"""

from __future__ import annotations

from agent_logger.sync.targets import TARGET_NAMES, build_target

__all__ = ["TARGET_NAMES", "build_target"]
