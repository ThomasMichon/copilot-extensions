"""Same-cell worktrees adapter; legacy selection stays with each caller."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

from ._peer_launch import ContextRefused, launch_prefix, validate_owner


def explicit_context() -> bool:
    """Whitespace is an explicit invalid context, not legacy mode."""
    return bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT", ""))


def validate_context() -> dict[str, Any] | None:
    """Validate even when a caller's explicit argument bypasses peer lookup."""
    if not explicit_context():
        return None
    raw_root = os.environ.get("AGENT_CODESPACES_HOME", "")
    if not raw_root:
        raise ContextRefused("Explicit CodeSpaces context requires AGENT_CODESPACES_HOME")
    root = Path(raw_root)
    try:
        return validate_owner("agent-codespaces", root, os.environ["COPILOT_EXTENSIONS_CONTEXT"])
    except (OSError, ValueError, ImportError) as error:
        raise ContextRefused(f"CodeSpaces installation context refused: {error}") from error


def run(
    *args: str, timeout: float = 15, cwd: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run the attributable peer, allowing only a genuinely absent optional peer.

    Failure to execute the validation boundary is a refusal too. Exit 126 is
    reserved by that boundary; ordinary peer command errors retain their meaning.
    """
    own = validate_context()
    if own is None:
        raise ValueError("Same-cell worktrees invocation requires explicit context")
    peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    if not peer_root.exists() and not peer_root.is_symlink():
        return None
    prefix = launch_prefix(
        "agent-codespaces", Path(own["pluginRoot"]),
        os.environ["COPILOT_EXTENSIONS_CONTEXT"], "agent-worktrees",
    )
    try:
        result = subprocess.run(
            [*prefix, *args], capture_output=True, encoding="utf-8",
            timeout=timeout, cwd=cwd, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ContextRefused(f"Same-cell worktrees invocation failed: {error}") from error
    if result.returncode == 126:
        raise ContextRefused(result.stderr.strip() or "Same-cell worktrees context refused")
    return result
