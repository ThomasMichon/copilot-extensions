"""Optional owner-key source through the vault's unlock-free cache CLI."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import signal
import subprocess
import threading
import time

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

log = logging.getLogger("agent-worktrees")
ARGV_ENV = "AGENT_WORKTREES_IDENTITY_VAULT_ARGV"
ENTRY_ENV = "AGENT_WORKTREES_IDENTITY_VAULT_ENTRY"
DEFAULT_ENTRY = "Identity/PR-attribution"
_READ_TIMEOUT = 15


def _wait_without_reaping(pid: int) -> int:
    deadline = time.monotonic() + 2
    while True:
        # Retain the leader's PID/PGID until group cleanup; wait() would release it.
        status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if status is not None:
            return status.si_status
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired("vault key reader", 2)
        time.sleep(0.01)


def _read_key_output(argv: list[str]) -> bytes:
    if os.name != "nt" and not all(
        hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        raise RuntimeError("vault key reader requires a non-reaping POSIX exit observer")
    process, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, **no_window_kwargs(),
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    done = threading.Event()
    output: list[bytes] = []

    def read():
        try:
            output.append(process.stdout.read(129))
        finally:
            done.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    owns_group_identity = True
    try:
        if not done.wait(_READ_TIMEOUT) or not output or len(output[0]) > 128:
            raise ValueError("vault key output invalid or timed out")
        if os.name == "nt":
            status = process.wait(timeout=2)
        else:
            try:
                status = _wait_without_reaping(process.pid)
            except ChildProcessError:
                owns_group_identity = False
                raise ValueError("vault key reader identity no longer owned") from None
        if status:
            raise ValueError("vault cache read failed")
        return output[0]
    finally:
        if job is not None:
            job.close()
        elif os.name != "nt" and owns_group_identity:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                log.warning("Vault key reader process-group cleanup failed.")
                process.kill()
        elif os.name == "nt" and process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("Vault key reader did not exit within cleanup deadline.")
        reader.join(timeout=2)
        if not reader.is_alive():
            process.stdout.close()


def load_cached_vault_key() -> bytes | None:
    """Fetch one common owner's key, never unlock or create a key implicitly.

    The explicit argv is the operator's payload-local vault invocation, not
    a shell command or PATH lookup. Vault selection and local cache enablement
    use agent-vault's own configuration. No output or subprocess error detail
    containing credentials is ever included in diagnostics.
    """
    encoded = os.environ.get(ARGV_ENV)
    if encoded is None:
        return None
    try:
        argv = json.loads(encoded)
        if (
            not isinstance(argv, list) or not argv or len(argv) > 16
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
            or not os.path.isabs(argv[0])
        ):
            raise ValueError("invalid vault argv")
        entry = os.environ.get(ENTRY_ENV, DEFAULT_ENTRY)
        if not entry or entry.startswith("-") or any(char in entry for char in "\r\n\x00"):
            raise ValueError("invalid vault entry")
        output = _read_key_output([*argv, "get", entry, "password", "--cache-only"])
        key = base64.b64decode(output.strip(), validate=True)
        if len(key) != 32:
            raise ValueError("invalid identity key")
        return key
    except (OSError, ValueError, RuntimeError, binascii.Error, subprocess.TimeoutExpired):
        log.warning(
            "Encrypted PR attribution omitted: shared vault key unavailable; "
            "explicitly populate its protected cache after unlocking."
        )
        return None
