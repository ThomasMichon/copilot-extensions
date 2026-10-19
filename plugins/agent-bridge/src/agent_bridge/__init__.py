"""Agent Bridge -- persistent inter-agent communication service."""

# Fallback only for running from a source tree with no installed distribution.
_FALLBACK_VERSION = "0.4.1-dev3"


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
            package_version = _pkg_version("agent-bridge")
        except PackageNotFoundError:
            return _FALLBACK_VERSION
        from .source_build import effective_runtime_version

        return effective_runtime_version(package_version)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
