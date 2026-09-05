"""agent-codespaces -- GitHub Codespaces lifecycle, SSH, and credential relay."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("agent-codespaces")
except PackageNotFoundError:
    __version__ = "0.4.0-dev115"
