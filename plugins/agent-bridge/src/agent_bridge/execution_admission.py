"""Process-boundary ACP claims fenced against native incumbents."""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
from agent_procutil import no_window_flags
_CODESPACE_BUSY_EXIT = 75
_CODESPACE_COORDINATION_EXIT = 78
log = logging.getLogger("agent-bridge")


def _claim_codespace(
    codespace_name: str,
    owner: str,
    *,
    holder_ref: str | None = None,
    execution_id: str = "",
) -> tuple[str, str]:
    """Acquire the exclusive, worktree-keyed CodeSpace claim before the
    Session-Host transport is established (#897 Increment B step 2).

    Session-Host dispatch never runs ``agent-codespaces ssh``, so the
    direct-path claim enforcement is bypassed for a bridge dispatch -- this is
    where the daemon closes that gap. Shells the ``agent-codespaces claim`` seam
    rather than importing ``agent_codespaces`` in the bridge venv (#796), so the
    two separately-versioned plugin venvs stay decoupled; mirrors
    ``gh_account``'s shell-out-to-a-sibling-binstub pattern.

    Returns ``("ok", "")`` on success or a degrade-safe skip,
    ``("conflict", detail)`` for a live owner conflict, or
    ``("coordination-rejected", detail)`` for a compatible binding rejection.
    """
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM") and not holder_ref:
        return "ok", ""
    if not codespace_name or (not owner and not holder_ref):
        return "ok", ""
    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    if not binstub:
        return "ok", ""
    creationflags = no_window_flags()
    command = [binstub, "claim", codespace_name]
    if owner:
        command.extend(["--owner", owner])
    if holder_ref:
        command.extend(["--holder-ref", holder_ref])
    if execution_id:
        command.extend(["--interaction-mode", "acp", "--execution-id", execution_id, "--generation", execution_id])
    try:
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=210 if execution_id else 30,
            creationflags=creationflags,
        )
    except Exception as exc:
        log.info("CodeSpace claim skipped for %s: %s", codespace_name, exc)
        return (
            ("coordination-rejected", "CodeSpace ownership could not be verified")
            if execution_id else ("ok", "")
        )
    if result.returncode == _CODESPACE_BUSY_EXIT:
        return "conflict", (result.stderr or result.stdout or "").strip()
    if result.returncode == _CODESPACE_COORDINATION_EXIT:
        return (
            "coordination-rejected",
            (result.stderr or result.stdout or "").strip(),
        )
    if result.returncode != 0:
        # Any other non-zero is a bookkeeping error, not a conflict -- never
        # block the dispatch on it (degrade-safe, mirroring the direct path).
        log.info(
            "CodeSpace claim for %s exited %s: %s",
            codespace_name, result.returncode, (result.stderr or "").strip(),
        )
        if execution_id:
            return "coordination-rejected", (result.stderr or result.stdout or "").strip()
    return "ok", ""


def _release_codespace_claim(codespace_name: str, owner: str, *, execution_id: str = "") -> bool:
    """Release a Session-Host CodeSpace claim and report success.

    Ordinary teardown remains best-effort, while destructive parity rollback
    uses the return value to avoid claiming cleanup before ownership is gone.
    """
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        return True
    if not owner or not codespace_name:
        return True
    binstub = shutil.which("agent-codespaces")  # marketplace-isolation: allow provider-management
    if not binstub:
        return False
    creationflags = no_window_flags()
    try:
        command = [binstub, "release-claim", codespace_name, "--owner", owner]
        if execution_id:
            command += ["--execution-id", execution_id, "--generation", execution_id]
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=30,
            creationflags=creationflags,
        )
    except Exception:
        return False
    return result.returncode == 0
