"""Optional, runtime-qualified Sync progress over the existing engine owner."""

from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from . import engine_client, engine_process, picker_review
from .engine_environment import _engine_environment

COMMUNICATION_TIMEOUT = 30
EXECUTION_TIMEOUT = 3600
_MAX_FRAME = 8 * 1024 * 1024
_MAX_STDERR = 256 * 1024


@contextmanager
def _output_directory():
    temporary = tempfile.TemporaryDirectory(prefix="worktree-sync-")
    retained = False
    try:
        yield Path(temporary.name)
    except engine_process.EngineCleanupError as error:
        retained = True
        temporary._finalizer.detach()
        error.output_directory = Path(temporary.name)
        raise
    finally:
        if not retained:
            temporary.cleanup()


def result_ok(result):
    """Keep terminal and baseline Sync success semantics identical."""
    if "ok" in result:
        return result["ok"] is True
    return bool(result.get("updated")) or result.get("reason") == "up-to-date"


def supports_progress(command):
    """Read support only beside the exact attributed Python runtime."""
    if not (
        command
        and len(command) == 5
        and Path(command[0]).is_absolute()
        and command[1] == "-X"
        and command[2].startswith("pycache_prefix=")
        and command[3:] == ["-m", "agent_worktrees"]
    ):
        return False
    slot = Path(command[0]).parent.parent
    pattern = (
        "Lib/site-packages/agent_worktrees/sync-progress-v1.json"
        if os.name == "nt"
        else "lib/python*/site-packages/agent_worktrees/sync-progress-v1.json"
    )
    try:
        paths = list(slot.glob(pattern))
        if len(paths) != 1:
            return False
        path = paths[0]
        if not path.resolve().is_relative_to(slot.resolve()) or path.stat().st_size > 65536:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    return (
        isinstance(data, dict)
        and data.get("contract") == "agent-worktrees.sync-progress"
        and type(data.get("version")) is int
        and data["version"] == 1
        and data.get("envelope") == "d4"
        and data.get("terminal_result") is True
        and isinstance(data.get("operations"), list)
        and "sync" in data["operations"]
        and isinstance(data.get("options"), list)
        and all(option in data["options"] for option in ("--json", "--stream"))
    )


class _Frames:
    def __init__(self, report, communication_timeout):
        self.report = report
        self.communication_timeout = communication_timeout
        self.started = self.contact = time.monotonic()
        self.message = "Waiting for Sync progress"
        self.terminal = None
        self.error = None
        self.pending = bytearray()

    def feed(self, chunk):
        self.pending.extend(chunk)
        while b"\n" in self.pending:
            line, _, self.pending = self.pending.partition(b"\n")
            self.contact = time.monotonic()
            if self.error:
                continue
            try:
                if len(line) > _MAX_FRAME:
                    raise ValueError
                frame = json.loads(line)
                if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
                    raise ValueError
                if self.terminal is not None:
                    raise ValueError
                kind = frame["type"]
                if kind in ("done", "error"):
                    if not isinstance(frame.get("result"), dict):
                        raise ValueError
                    self.terminal = frame
                elif kind == "progress":
                    if "msg" in frame and not isinstance(frame["msg"], str):
                        raise ValueError
                    if "msg" in frame:
                        self.message = frame["msg"]
            except (ValueError, UnicodeError, RecursionError):
                self.error = "invalid Sync progress frame"
        if len(self.pending) > _MAX_FRAME:
            self.pending.clear()
            self.error = "Sync progress frame exceeded its byte limit"

    def observe(self):
        now = time.monotonic()
        self.report(
            {
                "message": self.message,
                "communication_lost": now - self.contact >= self.communication_timeout,
                "elapsed": now - self.started,
            }
        )

    def result(self, returncode, stderr, failure):
        terminal = self.terminal
        result = dict(terminal["result"]) if terminal else {}
        reason = failure or self.error
        if not reason and (self.pending or terminal is None):
            reason = "Sync ended without a complete terminal result"
        if not reason:
            ok = result_ok(result)
            if (terminal["type"] == "done") != ok or (returncode == 0) != ok:
                reason = "Sync terminal result contradicts its process outcome"
        if reason:
            original_reason = result.get("reason")
            result.update(ok=False, reason=reason, outcome="unknown")
        else:
            original_reason = None
        if stderr or reason:
            result["detail"] = "\n".join(
                str(part)
                for part in (
                    original_reason,
                    result.get("detail"),
                    stderr,
                    (
                        f"Last stage: {self.message}; elapsed {time.monotonic() - self.started:.1f}s; "
                        f"last contact {time.monotonic() - self.contact:.1f}s ago"
                    )
                    if reason
                    else None,
                )
                if part
            )
        return result


def run(
    project,
    worktree_id,
    *,
    report=lambda _update: None,
    cancelled=None,
    communication_timeout=COMMUNICATION_TIMEOUT,
    execution_timeout=EXECUTION_TIMEOUT,
):
    """Run once, preserving the selected command, complete result and ownership."""
    picker_review.require_writable()
    if cancelled is not None and cancelled.is_set():
        return {
            "ok": False,
            "reason": "Sync owner closed before execution",
            "outcome": "not-started",
        }
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        for value in (communication_timeout, execution_timeout)
    ):
        raise ValueError("Sync communication and execution budgets must be finite and positive")
    command = engine_client.engine_base_command()
    args = ["sync", "--paired", "--worktree-id", worktree_id, "--json"]
    if not supports_progress(command):

        def owned_baseline(argv, timeout):
            try:
                return engine_process.run(
                    argv,
                    timeout=timeout,
                    cancelled=cancelled,
                    confirm_cleanup=True,
                    env=_engine_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
            except engine_process.EngineCleanupError as error:
                from .engine_progress import json_with_failure_detail

                error.sync_result = json.loads(
                    json_with_failure_detail(
                        getattr(error, "stdout", None),
                        str(getattr(error, "stderr", "") or "")[:_MAX_STDERR],
                        failed=True,
                    )
                )
                raise

        try:
            return engine_client.run_json(
                project,
                args,
                timeout=120,
                allow_nonzero=True,
                **({"engine_command": command} if command is not None else {}),
                **({"runner": owned_baseline} if cancelled is not None else {}),
            )
        except engine_client.EngineRetirementError as error:
            if isinstance(error.__cause__, engine_process.EngineCleanupError):
                raise error.__cause__ from None
            raise
        except engine_client.EngineError:
            if cancelled is None or not cancelled.is_set():
                raise
            return {
                "ok": False,
                "reason": "Sync owner closed; execution interrupted",
                "outcome": "unknown",
            }
    argv = [*command, *(["--project", project] if project else []), *args, "--stream"]
    frames = _Frames(report, communication_timeout)
    stopped = threading.Event()
    overflow = threading.Event()
    stop_requested = threading.Event()
    failure = None
    reader_errors = []
    returncode = None
    cleanup_error = None
    # Files drain both streams without a pipe-pressure deadlock. The existing
    # process owner retains its Job/group and finite execution/teardown budget.
    with _output_directory() as directory:
        out_path, err_path = (directory / name for name in ("stdout", "stderr"))
        with out_path.open("wb") as stdout, err_path.open("wb") as stderr:

            def receive():
                try:
                    with out_path.open("rb") as reader:
                        while True:
                            if cancelled is not None and cancelled.is_set():
                                stop_requested.set()
                            chunk = reader.read(65536)
                            if chunk:
                                frames.feed(chunk)
                            frames.observe()
                            if (
                                out_path.stat().st_size > 2 * _MAX_FRAME
                                or err_path.stat().st_size > _MAX_STDERR
                            ):
                                overflow.set()
                                stop_requested.set()
                                return
                            if stopped.is_set() and not chunk:
                                return
                            stopped.wait(0.05)
                except Exception as error:
                    reader_errors.append(str(error))
                    overflow.set()
                    stop_requested.set()

            reader = threading.Thread(target=receive, name="sync-progress", daemon=False)
            reader.start()
            try:
                result = engine_process.run(
                    argv,
                    timeout=execution_timeout,
                    cancelled=stop_requested,
                    confirm_cleanup=True,
                    env=_engine_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
                returncode = result.returncode
            except subprocess.TimeoutExpired:
                failure = (
                    f"Sync execution budget expired after {execution_timeout}s; outcome unknown"
                )
            except InterruptedError:
                failure = (
                    "Sync owner closed; execution interrupted; outcome unknown"
                    if cancelled is not None and cancelled.is_set()
                    else "Sync output exceeded its byte budget; outcome unknown"
                )
            except engine_process.EngineCleanupError as error:
                cleanup_error = error
                returncode = getattr(error, "returncode", None)
            except OSError as error:
                failure = f"Sync process unavailable: {error}; outcome unknown"
            finally:
                stopped.set()
                reader.join()
        with err_path.open("rb") as reader:
            detail = reader.read(_MAX_STDERR).decode("utf-8", errors="replace")
        if cleanup_error is not None:
            cleanup_error.sync_result = frames.result(
                returncode,
                detail,
                f"Sync cleanup unconfirmed: {cleanup_error}",
            )
            raise cleanup_error
    if reader_errors:
        failure = "Sync progress observation failed; outcome unknown"
        detail = "\n".join((detail, *reader_errors))
    if overflow.is_set() and failure is None:
        failure = "Sync output exceeded its byte budget; outcome unknown"
    return frames.result(returncode, detail, failure)
