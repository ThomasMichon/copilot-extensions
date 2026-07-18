"""Recover a CodeSpace's Copilot session-state into the agent-logger hub.

Pulls ``~/.copilot`` session data (the ``session-state/`` tree plus the
``session-store.db*`` index files -- never credentials, keys, or settings) off a
CodeSpace over the multiplexed SSH connection, then lands it in the configured
agent-logger storage target under ``.codespaces/<name>`` by shelling out to the
``session-sync push`` CLI (agent-logger).

This keeps the two plugins decoupled with no shared venv: agent-codespaces owns
the CodeSpace/SSH pull; agent-logger owns the storage pattern. The binary
``tar`` payload is transferred base64-wrapped in sentinels so stray banner/log
text on the SSH channel can never corrupt it (a real failure mode -- see the
dotfiles ``sync-copilot-sessions.ps1`` history).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import string
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from .codespace_config import CodespaceSource

log = logging.getLogger("agent-codespaces")

# A Shutdown CodeSpace boots on connect; match the SSH command's patience.
_BOOT_TIMEOUT = float(os.environ.get("AGENT_CODESPACES_BOOT_TIMEOUT", "180"))

_B64_START = "===ACS_SESSION_B64_START==="
_B64_END = "===ACS_SESSION_B64_END==="
_B64_CHARS = frozenset(string.ascii_letters + string.digits + "+/=")

# Remote: tar ONLY the session-state tree + db index files (never the rest of
# ~/.copilot -- OAuth/credential state, keys, settings), gzip + base64, wrapped
# in sentinels. Emits nothing between the sentinels when there are no sessions.
_PULL_CMD = (
    "cd ~/.copilot 2>/dev/null && "
    "files=$(ls -d session-state session-store.db session-store.db-wal "
    'session-store.db-shm 2>/dev/null) && [ -n "$files" ] && '
    "{ echo " + _B64_START + "; tar czf - $files 2>/dev/null | base64 -w0; "
    "echo; echo " + _B64_END + "; } || true"
)


def _extract_b64(text: str) -> str:
    """Return the base64 payload between the sentinels, keeping only valid
    base64 characters (robust to interleaved log lines)."""
    out: list[str] = []
    capture = False
    for line in text.splitlines():
        if _B64_START in line:
            capture = True
            continue
        if _B64_END in line:
            break
        if capture:
            out.append("".join(ch for ch in line if ch in _B64_CHARS))
    return "".join(out)


def find_session_sync() -> str | None:
    """Locate the agent-logger ``session-sync`` console script on PATH."""
    return shutil.which("session-sync")


async def _connect_with_retry(manager, name: str, *, timeout: float) -> None:
    """ensure_connected with boot-patience retry (a Shutdown CS boots here)."""
    source = CodespaceSource(name)
    deadline = time.monotonic() + timeout
    backoff = 3.0
    while True:
        try:
            await manager.ensure_connected(name, source, [])
            return
        except (ConnectionError, TimeoutError) as exc:
            if time.monotonic() + backoff >= deadline:
                raise
            log.info("CodeSpace %s not ready (booting?): %s -- retry in %.0fs",
                     name, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 20.0)


async def _pull_tar_bytes(manager, name: str, *, timeout: float) -> bytes | None:
    """Run the remote tar+base64 and return decoded gzip-tar bytes (or None)."""
    result = await manager.exec_command(name, _PULL_CMD, timeout=timeout)
    b64 = _extract_b64(result.stdout or "")
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except ValueError:  # binascii.Error is a ValueError subclass
        log.warning("session pull from %s produced an undecodable payload", name)
        return None


def _stage_and_push(tar_bytes: bytes, name: str, *, verbose: bool) -> dict:
    """Extract the pulled archive to a staging dir (validating it), then push it
    into the agent-logger hub under ``.codespaces/<name>``."""
    with tempfile.TemporaryDirectory(prefix=f"acs-sessions-{name}-") as tmp:
        staging = Path(tmp)
        tar_path = staging / "sessions.tar.gz"
        tar_path.write_bytes(tar_bytes)
        try:
            with tarfile.open(tar_path, "r:gz") as tf:
                tf.extractall(staging)  # noqa: S202 - own CodeSpace session data
        except (tarfile.TarError, OSError) as exc:
            return {"ok": False, "session_count": 0,
                    "detail": f"invalid/corrupt session archive: {exc}"}
        finally:
            tar_path.unlink(missing_ok=True)

        # Drop a corrupt session-store.db rather than store a bad index.
        db = staging / "session-store.db"
        if db.is_file():
            with open(db, "rb") as fh:
                header_ok = fh.read(15) == b"SQLite format 3"
            if not header_ok:
                log.warning("session-store.db failed SQLite header check; dropping")
                db.unlink()

        ss = staging / "session-state"
        count = sum(1 for d in ss.iterdir() if d.is_dir()) if ss.is_dir() else 0
        ok, detail = _push_via_session_sync(staging, f".codespaces/{name}", verbose=verbose)
        return {"ok": ok, "session_count": count, "detail": detail}


def _is_stale_session_sync(stderr: str) -> bool:
    """True when session-sync rejected the ``push`` subcommand.

    Indicates the deployed agent-logger predates ``session-sync push`` -- a
    version skew where agent-codespaces (which calls ``push``) is newer than the
    installed agent-logger. The CLI prints e.g.
    ``argument command: invalid choice: 'push' (choose from run, status, doctor)``.
    See dotfiles#246.
    """
    return "invalid choice: 'push'" in (stderr or "").lower()


def _push_via_session_sync(staging: Path, machine_label: str, *, verbose: bool) -> tuple[bool, str]:
    """Shell out to ``session-sync push`` (agent-logger) to land *staging* in
    the configured hub target under *machine_label*."""
    exe = find_session_sync()
    if not exe:
        return False, ("session-sync CLI not found on PATH "
                       "(agent-logger not installed?)")
    cmd = [exe, "push", "--source", str(staging), "--machine", machine_label]
    if verbose:
        cmd.append("--verbose")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        err = (proc.stderr or out).strip()
        if _is_stale_session_sync(err):
            return False, (
                "deployed session-sync is stale (no 'push' subcommand) -- "
                "agent-logger is older than agent-codespaces. Upgrade it to "
                "restore session recovery: run the agent-logger installer "
                "(plugins/agent-logger/scripts/install.ps1 install on Windows, "
                "install.sh install on Linux/WSL) or `agent-worktrees update`. "
                f"[session-sync: {err}]"
            )
        return False, f"session-sync push failed: {err}"
    return True, out


def sync_codespace_sessions(
    name: str,
    *,
    timeout: float = 300.0,
    verbose: bool = False,
    skip_if_shutdown: bool = False,
) -> dict:
    """Pull a CodeSpace's Copilot session-state and land it in the agent-logger
    hub under ``.codespaces/<name>``.

    Returns a result dict: ``{ok, session_count, detail, skipped?}``. Never
    raises for routine connect/pull failures -- callers (delete hook, finalize)
    treat a failed sync as non-fatal and decide whether to proceed.

    ``skip_if_shutdown`` -- when True, if the CodeSpace is already ``Shutdown``,
    return a no-op success **without booting it**. A Shutdown box's sessions were
    already captured when it was stopped/finalized, so booting it just to re-pull
    is wasteful and (on a busy account) trips the "too many codespaces running"
    quota. The *preserving* callers (``finalize`` without ``--delete``, ``stop``)
    pass this; the *destructive* callers (``delete``, ``finalize --delete``, the
    final pre-prune pull) do NOT -- they must recover even a Shutdown box before
    it is gone, booting if necessary.
    """
    if skip_if_shutdown:
        try:
            from .lifecycle import _SHUTDOWN_STATE, list_codespaces

            state = next(
                (cs.state for cs in list_codespaces() if cs.name == name), None
            )
        except RuntimeError:
            state = None  # can't list (auth/network) -> fall through, try normally
        if state == _SHUTDOWN_STATE:
            log.info(
                "CodeSpace %s is Shutdown; skipping boot-to-recover "
                "(sessions were captured when it was stopped/finalized)", name,
            )
            return {"ok": True, "skipped": True, "session_count": 0,
                    "detail": "already Shutdown; skipped boot-to-recover"}

    from ssh_manager import ConnectionManager, TargetBusyError, TargetLock

    lock = TargetLock(name, op="session-sync")
    try:
        lock.acquire(force=False)
    except TargetBusyError as busy:
        return {"ok": False, "skipped": True, "session_count": 0,
                "detail": f"target busy, skipped sync: {busy}"}

    manager = ConnectionManager()

    async def _run() -> dict:
        try:
            await _connect_with_retry(manager, name, timeout=_BOOT_TIMEOUT)
        except (ConnectionError, TimeoutError, RuntimeError) as exc:
            # RuntimeError covers the SSH-config-fetch timeout / gh failures
            # (codespace_config) that an unbootable CodeSpace raises -- treat
            # them as a connect failure, never propagate, so a --force
            # delete/finalize is not blocked by an unreachable target (#155).
            return {"ok": False, "session_count": 0, "detail": f"could not connect: {exc}"}
        try:
            tar_bytes = await _pull_tar_bytes(manager, name, timeout=timeout)
        finally:
            await manager.disconnect(name)
        if not tar_bytes:
            return {"ok": True, "session_count": 0, "detail": "no sessions on CodeSpace"}
        return _stage_and_push(tar_bytes, name, verbose=verbose)

    try:
        return asyncio.run(_run())
    except Exception as exc:  # recovery must never block delete (#155)
        # The contract is "never raise for routine connect/pull failures". Any
        # unexpected error here (e.g. an SSH/relay failure surfacing as a plain
        # Exception) must still return a failed-recovery result so a --force
        # finalize/delete can proceed rather than aborting.
        return {"ok": False, "session_count": 0,
                "detail": f"session recovery error: {exc}"}
    finally:
        lock.release()
