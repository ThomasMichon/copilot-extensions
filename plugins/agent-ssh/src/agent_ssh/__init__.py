"""agent-ssh runtime package."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("agent-ssh")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0+dev"
