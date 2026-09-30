"""agent-machines -- portable ``restore-machinestate`` for Copilot CLI.

A generic engine that converges the current machine to desired state declared in
in-repo **requirement packages**. The engine is public; sensitive, OS-mutating
modules and per-machine data stay in each harness repo.
"""

from __future__ import annotations

# Fallback only for running from a source tree with no installed distribution.
_FALLBACK_VERSION = "0.1.15-dev1"


def __getattr__(name: str) -> str:
    # ``__version__`` is resolved lazily: ``importlib.metadata.version`` pulls in
    # a substantial stdlib import chain that every hook-invoked/cold CLI call
    # would otherwise pay even for subcommands that never touch the version
    # string. Mirrors agent-mcp's own ``__getattr__`` fix (see the
    # ``agent-cli-lazy-dispatch`` effort).
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        try:
            return _pkg_version("agent-machines")
        except PackageNotFoundError:  # running from source without an install
            return _FALLBACK_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
