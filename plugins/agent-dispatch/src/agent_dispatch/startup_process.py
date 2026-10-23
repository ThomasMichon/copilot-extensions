"""Stream a single Bridge observation without granting target cancellation authority."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import subprocess
import threading

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from agent_procutil import owned_process
from agent_procutil.startup import PROGRESS_PREFIX, StartupClock, StartupExpiry, StartupTimeouts

log = logging.getLogger(__name__)
CAPTURE_LIMIT = 1024 * 1024


class StartupOutcomeUnknown(subprocess.TimeoutExpired):
    """Observation ended without proving admission or cancellation; never replay."""

    retryable = False

    def __init__(self, expiry: StartupExpiry, policy: StartupTimeouts, *,
                 output: str | None = "", stderr: str | None = "",
                 scope: str = "dispatch_cli", reason: str | None = None) -> None:
        self.clock = expiry.clock
        self.elapsed = expiry.elapsed
        self.silence = expiry.silence
        self.phase = expiry.phase
        self.scope = scope
        self.reason = reason
        super().__init__(
            "Bridge startup observation (outcome unknown; retryable=false)",
            policy.process if expiry.clock == "process" else policy.communication,
            output=output, stderr=stderr,
        )

    def __str__(self) -> str:
        failure = (
            f"{self.clock} timeout" if self.clock in ("communication", "process")
            else f"{self.clock} observation failure"
        )
        return (
            f"Bridge startup {failure}; outcome unknown; retryable=false; "
            f"scope={self.scope}; phase={self.phase}; elapsed={self.elapsed:.3f}s; "
            f"silence={self.silence:.3f}s"
        )


def run_startup(argv: list[str], *, input_text: str,
                policy: StartupTimeouts) -> subprocess.CompletedProcess:
    """Own only the local CLI tree, retaining custody through silence until exit/expiry."""
    clock = StartupClock(policy)
    windows = os.name == "nt"
    spawn = spawn_sync_in_kill_on_close_job
    if windows:
        from agent_procutil._windows_caller_owned import spawn
    else:
        owned_process.require_caller_owned_support()
    process, job = spawn(
        argv, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=False, bufsize=0, **no_window_kwargs(),
        **({} if windows else {"start_new_session": True}),
    )
    output = (bytearray(), bytearray())
    truncated = [False, False]
    lock = threading.Lock()
    changed = threading.Event()
    stopping = threading.Event()
    errors = []
    remote_expiry = None
    deadline_expiry = None
    last_phase = "startup"
    workers = []

    def receive(phase=None):
        nonlocal last_phase
        clock.receive(phase)
        if phase is not None:
            last_phase = phase

    def frame(line: bytes) -> None:
        nonlocal remote_expiry, deadline_expiry
        prefix = PROGRESS_PREFIX.encode()
        if not line.startswith(prefix):
            return
        try:
            packet = json.loads(line[len(prefix):])
        except (ValueError, RecursionError):
            return
        if not isinstance(packet, dict):
            return
        event = packet.get("event")
        phase = packet.get("phase")
        # Only bounded protocol identifiers, never arbitrary child text, reach diagnostics.
        phase = (
            phase if isinstance(phase, str)
            and re.fullmatch(r"[A-Za-z0-9_.:/ -]{1,120}", phase) else None
        )
        if phase is not None:
            receive(phase)
        if packet.get("version") == 1 and event in ("communication_timeout", "process_timeout"):
            remote_expiry = StartupExpiry(
                "process" if event == "process_timeout" else "communication",
                clock.elapsed, 0.0, phase or last_phase,
            )
            return
        if not clock.ready and clock.process_remaining() <= 0:
            deadline_expiry = clock.expired()
            return
        if packet.get("version") == 1 and event == "ready":
            clock.mark_ready()

    def read(stream, index: int) -> None:
        pending = bytearray()
        discard_line = False
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    if pending:
                        with lock:
                            frame(bytes(pending))
                    return
                with lock:
                    # All bytes from our CLI peer are contact, including its
                    # report of a failure at a different, inner clock hop.
                    receive()
                    raw_chunk = chunk
                    if index == 1:
                        if discard_line:
                            _head, separator, chunk = chunk.partition(b"\n")
                            discard_line = not separator
                        if not discard_line:
                            pending.extend(chunk)
                            while b"\n" in pending:
                                line, _, tail = pending.partition(b"\n")
                                pending[:] = tail
                                frame(bytes(line))
                            # An oversized diagnostic line is not a progress frame.
                            if len(pending) > CAPTURE_LIMIT:
                                pending.clear()
                                discard_line = True
                    if len(output[index]) + len(raw_chunk) > CAPTURE_LIMIT:
                        if not clock.ready:
                            raise StartupOutcomeUnknown(
                                StartupExpiry("capture", clock.elapsed, 0.0, last_phase),
                                policy, reason="output_limit",
                            )
                        if not truncated[index]:
                            log.warning(
                                "Bridge %s capture truncated after readiness; "
                                "retaining diagnostic tail only; observation continues",
                                ("stdout", "stderr")[index],
                            )
                            truncated[index] = True
                    output[index].extend(raw_chunk)
                    if len(output[index]) > CAPTURE_LIMIT:
                        del output[index][:-CAPTURE_LIMIT]
                    changed.set()
        except BaseException as error:
            if not stopping.is_set() or not isinstance(error, (OSError, ValueError)):
                with lock:
                    errors.append(error)
                changed.set()

    def write() -> None:
        try:
            data = memoryview(input_text.encode("utf-8"))
            while data:
                count = process.stdin.write(data[:65536])
                if not count:
                    raise OSError("Bridge stdin made no progress")
                data = data[count:]
        except BaseException as error:
            if not stopping.is_set() and not (
                isinstance(error, OSError) and error.errno in (errno.EPIPE, errno.EINVAL)
            ):
                with lock:
                    errors.append(error)
                changed.set()
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError) as error:
                if not stopping.is_set() and not (
                    isinstance(error, OSError) and error.errno in (errno.EPIPE, errno.EINVAL)
                ):
                    with lock:
                        errors.append(error)
                    changed.set()

    primary = None
    silence_reported = False
    try:
        for callback, args in (
            (read, (process.stdout, 0)), (read, (process.stderr, 1)), (write, ()),
        ):
            worker = threading.Thread(target=callback, args=args, daemon=True)
            workers.append(worker)
            worker.start()
        while True:
            with lock:
                expiry = deadline_expiry or clock.expired()
                if expiry and expiry.clock == "process":
                    raise StartupOutcomeUnknown(expiry, policy)
                if errors:
                    raise errors[0]
                if expiry and not silence_reported:
                    log.warning("%s", StartupOutcomeUnknown(expiry, policy))
                    silence_reported = True
                elif expiry is None:
                    silence_reported = False
            exited = process.poll() is not None if windows else (
                owned_process._observe_unreaped(process) is not None
            )
            if exited:
                break
            changed.wait(0.02)
            changed.clear()
    except BaseException as error:
        primary = error
    finally:
        stopping.set()
        try:
            if windows:
                job.terminate_and_wait(2.0)
            else:
                from agent_procutil._caller_owned import _confirm_group

                owned_process._retire_posix_group(process)
                _confirm_group(process)
            process.wait(timeout=2.0)
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=2.0)
                    if worker.is_alive():
                        raise RuntimeError("Bridge pipe worker did not settle")
        except BaseException as error:
            raise owned_process.OwnedCleanupError(process, job) from error
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        if windows:
            process.close()
            job.close()
    captured = tuple(
        bytes(data).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        for data in output
    )
    stdout, stderr = (
        None if incomplete else text for text, incomplete in zip(captured, truncated)
    )
    if isinstance(primary, StartupOutcomeUnknown):
        primary.output, primary.stderr = stdout, stderr
    if primary is not None:
        raise primary
    if deadline_expiry is not None:
        raise StartupOutcomeUnknown(deadline_expiry, policy, output=stdout, stderr=stderr)
    if errors:
        raise errors[0]
    if process.returncode == 70 or remote_expiry is not None:
        reason = None
        for text in (stdout, stderr):
            try:
                envelope = json.loads(text) if text else None
            except (ValueError, RecursionError):
                continue
            if isinstance(envelope, dict):
                detail = envelope.get("detail")
                candidates = (
                    envelope.get("reason"),
                    detail.get("reason") if isinstance(detail, dict) else None,
                )
                reason = next((
                    item for item in candidates if item in (
                        "admission_unknown", "startup_outcome_unknown", "outcome_unknown",
                        "startup_observation_unavailable", "startup_cursor_unavailable",
                    )
                ), reason)
        raise StartupOutcomeUnknown(
            remote_expiry or StartupExpiry("outcome", clock.elapsed, 0.0, last_phase),
            policy, output=stdout, stderr=stderr, scope="bridge_target", reason=reason,
        )
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.capture_truncated = tuple(
        name for name, incomplete in zip(("stdout", "stderr"), truncated) if incomplete
    )
    result.diagnostic_tails = {
        name: text for name, text, incomplete
        in zip(("stdout", "stderr"), captured, truncated) if incomplete
    }
    return result
