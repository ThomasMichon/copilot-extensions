"""agent-ssh runtime package."""

# Fallback only for running from a source tree with no installed distribution.
_FALLBACK_VERSION = "0.1.19-dev1"


def __getattr__(name: str) -> str:
    # ``__version__`` is resolved lazily: ``importlib.metadata.version`` pulls in
    # a substantial stdlib import chain (importlib.resources, email, hashlib,
    # ...) that every hook-invoked/cold CLI call would otherwise pay even for
    # subcommands that never touch the version string. Mirrors agent-mcp's own
    # ``__getattr__`` fix (see the ``agent-cli-lazy-dispatch`` effort).
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        try:
            return _pkg_version("agent-ssh")
        except PackageNotFoundError:  # running from source without install
            return _FALLBACK_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
