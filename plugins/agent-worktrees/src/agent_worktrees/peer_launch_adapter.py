"""Same-cell peer-launch adapter for agent-worktrees-owned sibling callbacks."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any

from agent_procutil import no_window_kwargs

from ._peer_launch import ContextRefused, launch_prefix, validate_owner

_OWNER = "agent-worktrees"


def explicit_context() -> bool:
    """Whitespace is an explicit invalid context, not legacy mode."""
    return bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", ""))


def context_reference() -> tuple[Path, str] | None:
    """Parse the explicit owner reference; the caller must still validate it."""
    if not explicit_context():
        return None
    context = os.environ["COPILOT_EXTENSIONS_CONTEXT"]
    stripped = context.lstrip()
    if stripped.startswith(("{", "[")) or stripped == "null":
        decoded = json.loads(context)
        if not isinstance(decoded, dict):
            raise ValueError("Explicit inline context must be a JSON object")
        pointer = decoded.get("installReceipt")
    else:
        pointer = context
    if not isinstance(pointer, str) or not pointer:
        raise ValueError("Explicit context must name its installation receipt")
    # A captured reference may belong to another OS; preserve its parent.
    if "\\" in pointer:
        root = Path(str(PureWindowsPath(pointer).parent))
    else:
        root = Path(pointer).expanduser().parent
    return root, context


def validate_context() -> dict[str, Any] | None:
    """Validate the current agent-worktrees owner context, if explicit."""
    try:
        reference = context_reference()
        if reference is None:
            return None
        root, context = reference
        return validate_owner(_OWNER, root, context)
    except (OSError, ValueError, ImportError) as error:
        raise ContextRefused(f"agent-worktrees installation context refused: {error}") from error


def run(
    peer: str, *args: str, timeout: float, cwd: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run a peer through the validated same-cell launcher.

    Returns ``None`` only when the target peer is genuinely absent from this
    cell. Validation-boundary failures raise :class:`ContextRefused` and must
    never be silently downgraded to legacy invocation.
    """
    own = validate_context()
    if own is None:
        raise ValueError("Same-cell peer launch requires explicit context")
    peer_root = Path(own["cellRoot"]) / "plugins" / peer
    if not peer_root.exists() and not peer_root.is_symlink():
        return None
    prefix = launch_prefix(
        _OWNER,
        Path(own["pluginRoot"]),
        os.environ["COPILOT_EXTENSIONS_CONTEXT"],
        peer,
    )
    try:
        result = subprocess.run(
            [*prefix, *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as error:
        raise ContextRefused(f"Same-cell peer invocation failed: {error}") from error
    if result.returncode == 126:
        raise ContextRefused(result.stderr.strip() or "Same-cell peer context refused")
    return result
