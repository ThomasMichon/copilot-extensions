"""agent-machines -- portable ``restore-machinestate`` for Copilot CLI.

A generic engine that converges the current machine to desired state declared in
in-repo **requirement packages**. The engine is public; sensitive, OS-mutating
modules and per-machine data stay in each harness repo.
"""

from __future__ import annotations

__version__ = "0.1.0-dev4"
