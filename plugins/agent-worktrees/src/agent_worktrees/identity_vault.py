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
# Owning payload gates validate installation provenance before entering Python;
# their cold native-shell startup is part of this bounded subprocess contract.
_PAYLOAD_READ_TIMEOUT = 45


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


def _read_command_output(
    argv: list[str], *, env: dict[str, str] | None = None,
    timeout: float | None = None, limit: int = 128, input_data: bytes | None = None,
) -> tuple[int, bytes]:
    if input_data is not None and (not isinstance(input_data, bytes) or len(input_data) > 32768):
        raise ValueError("vault adapter input is invalid or oversized")
    if os.name != "nt" and not all(
        hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        raise RuntimeError("vault key reader requires a non-reaping POSIX exit observer")
    process, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL if input_data is None else subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, **no_window_kwargs(),
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    done = threading.Event()
    output: list[bytes] = []
    input_failed = threading.Event()
    input_done = threading.Event()

    def read():
        try:
            output.append(process.stdout.read(limit + 1))
        finally:
            done.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    writer = None
    if input_data is not None:
        def write():
            try:
                with process.stdin:
                    process.stdin.write(input_data)
            except (OSError, ValueError):
                input_failed.set()
            finally:
                input_done.set()

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
    owns_group_identity = True
    try:
        if not done.wait(_READ_TIMEOUT if timeout is None else timeout) or not output or len(output[0]) > limit:
            raise ValueError("vault key output invalid or timed out")
        if writer is not None and (not input_done.wait(2) or input_failed.is_set()):
            raise ValueError("vault adapter input was not accepted")
        if os.name == "nt":
            status = process.wait(timeout=2)
        else:
            try:
                status = _wait_without_reaping(process.pid)
            except ChildProcessError:
                owns_group_identity = False
                raise ValueError("vault key reader identity no longer owned") from None
        return status, output[0]
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
        if writer is not None:
            writer.join(timeout=2)
            if writer.is_alive():
                log.warning("Vault adapter input writer did not exit within cleanup deadline.")
        if not reader.is_alive():
            process.stdout.close()


def _read_key_output(
    argv: list[str], *, env: dict[str, str] | None = None, timeout: float | None = None,
) -> bytes:
    status, output = _read_command_output(argv, env=env, timeout=timeout)
    if status:
        raise ValueError("vault cache read failed")
    return output


def load_cached_vault_key(source: dict | None = None) -> bytes | None:
    """Fetch one common owner's key, never unlock or create a key implicitly.

    The explicit argv is the operator's payload-local vault invocation, not
    a shell command or PATH lookup. Vault selection and local cache enablement
    use agent-vault's own configuration. No output or subprocess error detail
    containing credentials is ever included in diagnostics.
    """
    try:
        from .identity_vault_settings import load_settings, source_environment, validate_argv, validate_settings

        encoded = os.environ.get(ARGV_ENV) if source is None else None
        if source is not None:
            settings = validate_settings(source)
        else:
            settings = load_settings() if encoded is None else None
        if encoded is None and settings is None:
            return None
        argv = validate_argv(json.loads(encoded) if encoded is not None else settings["argv"])
        entry = os.environ.get(ENTRY_ENV, DEFAULT_ENTRY) if encoded is not None else settings["entry"]
        if not entry or entry.startswith("-") or any(char in entry for char in "\r\n\x00"):
            raise ValueError("invalid vault entry")
        command = [*argv, "get", entry, "password", "--cache-only"]
        output = (
            _read_key_output(command, env=source_environment(settings), timeout=_PAYLOAD_READ_TIMEOUT)
            if settings is not None else _read_key_output(command)
        )
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
