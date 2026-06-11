"""agent-containers -- local Docker dev-container fleet + lease broker.

Manages a persistent fleet of local dev containers (Docker Desktop WSL2
backend), brokers exclusive *leases* so an effort can borrow a container
without two parallel worktrees driving the same one, and exposes a
``container:<name>`` namespace resolver to agent-bridge that dispatches a
Copilot agent into the container over ``docker exec``.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("agent-containers")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0+dev"
