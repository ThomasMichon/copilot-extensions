"""Experimental reference implementation; see the parent README."""

from __future__ import annotations


_FALLBACK_VERSION = "0.1.15-dev1"


def __getattr__(name: str) -> str:


    if name == "__version__":
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        try:
            return _pkg_version("agent-vault")
        except PackageNotFoundError:
            return _FALLBACK_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
