"""agent-machines -- portable ``restore-machinestate`` for Copilot CLI.

A generic engine that converges the current machine to desired state declared in
in-repo **requirement packages**. The engine is public; sensitive, OS-mutating
modules and per-machine data stay in each harness repo.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("agent-machines")
except PackageNotFoundError:  # running from source without an install
    __version__ = "0.1.0-dev88"
