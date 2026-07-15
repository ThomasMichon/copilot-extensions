"""Neutral npm-package runner selection for ``server.npm`` bridges.

A stdio bridge can name an upstream by its **npm package** (``server.npm: <pkg>``)
instead of hardcoding a launcher in ``server.command``. At spawn time we resolve
the *fastest available* runner rather than committing to one in config:

* ``bunx`` -- Bun's package runner. It does not re-walk an already-cached
  dependency tree the way ``npx`` does on every invocation, so a cold
  ``bunx <pkg>`` reaches the server's ``initialize`` roughly twice as fast as
  ``npx -y <pkg>``, and it falls back to its cache when the registry is
  unreachable (where ``npx`` hangs). It needs no ``-y`` equivalent.
* ``npx -y`` -- always available wherever Node is, the neutral default.

**Neutrality invariant:** agent-mcp never *requires* bun. ``npx`` is always a
valid runner, so any consumer works out of the box; ``bunx`` is a transparent
optimization used only when the host already provides it. Selection happens at
spawn (via :func:`shutil.which`), keeping :func:`agent_mcp.config.parse_config`
pure/no-I/O -- the same seam the stdio transport already uses for
:func:`agent_mcp._exec.resolve_argv`.

``AGENT_MCP_NPM_RUNNER`` forces a specific runner (test hook + operator escape
hatch); a known runner keeps its correct arg prefix, an unknown value is used
bare. ``server.command`` remains the fully-explicit escape hatch and bypasses
this module entirely.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence

# Preference order, fastest-first. Each entry is (binary, args-before-package).
# bunx needs no `-y`; npx does (non-interactive auto-install).
_NPM_RUNNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bunx", ()),
    ("npx", ("-y",)),
)

# The neutral default runner when nothing resolves -- always valid wherever Node
# is installed; the spawn raises a clear FileNotFoundError if truly absent.
_DEFAULT_RUNNER: tuple[str, tuple[str, ...]] = ("npx", ("-y",))

_FORCE_ENV = "AGENT_MCP_NPM_RUNNER"


def resolve_npm_command(
    package: str,
    args: Sequence[str] = (),
    *,
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Resolve ``server.npm`` to a concrete argv: ``[runner, *prefix, package, *args]``.

    Picks the fastest runner whose binary is found on ``PATH`` (``bunx`` then
    ``npx``). ``AGENT_MCP_NPM_RUNNER`` overrides the choice. If no candidate
    resolves, falls back to ``npx -y`` so the caller's spawn surfaces a clear
    error with the standard runner name.
    """
    if not package:
        raise ValueError("server.npm requires a non-empty package name")
    resolved_env = os.environ if env is None else env

    forced = resolved_env.get(_FORCE_ENV)
    if forced:
        # A known runner keeps its correct prefix; an unknown one is used bare.
        prefix = next((p for name, p in _NPM_RUNNERS if name == forced), ())
        return [forced, *prefix, package, *args]

    for binary, prefix in _NPM_RUNNERS:
        if which(binary):
            return [binary, *prefix, package, *args]

    default_bin, default_prefix = _DEFAULT_RUNNER
    return [default_bin, *default_prefix, package, *args]
