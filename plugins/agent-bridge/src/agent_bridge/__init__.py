"""Agent Bridge -- persistent inter-agent communication service."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("agent-bridge")
except PackageNotFoundError:
    __version__ = "0.0.0-unknown"
