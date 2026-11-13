"""Original-owner admission and native receipts around the retained core executable."""

from __future__ import annotations

from pathlib import Path
import secrets
import signal
import subprocess
import time
from collections.abc import Callable

from agent_procutil import no_window_kwargs

from .launcher import prepare_headed_command
from .receipt import NONCE_ENV, native_child
from ..session_host.osutil import process_start_time
from ..session_host.startup_admission import CONTEXT_ENV, StartupAdmissionServer


def execute(
    argv: list[str], *, session_id: str, attempt_directory: Path,
    state_file: Path, cwd: str, environment: dict[str, str],
    admit: Callable[[], None], spawned: Callable[[int, str], None],
    interactive: bool,
) -> tuple[int, bool]:
    """Only pre-coordinator failure can raise; later uncertainty retains the wait."""
    command = prepare_headed_command(
        argv, session_id=session_id, attempt_directory=attempt_directory,
        state_file=state_file, cwd=cwd,
    )
    nonce = secrets.token_hex(32)
    child_env = {**environment, NONCE_ENV: nonce}

    def check(request: dict) -> None:
        if (
            request["argv"] != argv or request["session_id"] != session_id
            or request["cwd"] != cwd
        ):
            raise ValueError("Original headed command changed before creation")
        admit()

    startup = StartupAdmissionServer(check, environment_keys=(), timeout=30)
    child_env[CONTEXT_ENV] = startup.context
    process = None
    previous_handlers = {}
    try:
        for signum in (signal.SIGINT, *([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])):
            previous_handlers[signum] = signal.signal(signum, lambda _signal, _frame: None)
        # headless-guard: allow: an interactive coordinator inherits the original caller pane
        process = subprocess.Popen(
            command, cwd=cwd, env=child_env,
            **({} if interactive else no_window_kwargs()),
        )
        certain = False
        try:
            birth = process_start_time(process.pid)
            if not birth:
                raise ValueError("Retained coordinator birth is unavailable")
            startup.start(process)
            deadline = time.monotonic() + 30
            while process.poll() is None and time.monotonic() < deadline:
                if state_file.exists():
                    pid, native_birth = native_child(
                        state_file, coordinator_pid=process.pid, coordinator_birth=birth,
                        session_id=session_id, nonce=nonce,
                    )
                    spawned(pid, native_birth)
                    certain = True
                    break
                time.sleep(0.02)
        except Exception:
            # Coordinator creation is not native creation, and an unreadable
            # receipt is never authority to replay or restore the original seed.
            certain = False
        finally:
            startup.close()
        while True:
            try:
                code = process.wait()
                return code, certain
            except KeyboardInterrupt:
                continue
    except OSError as error:
        if process is not None:
            raise ValueError("Headed outcome is unconfirmed; retain the original handoff") from error
        raise
    finally:
        startup.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
