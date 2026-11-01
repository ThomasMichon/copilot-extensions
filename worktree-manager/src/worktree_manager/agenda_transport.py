"""Optional held CLI read, with caller-owned processes and bounded pipe delivery."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import signal
import subprocess
import sys
from threading import Event, Thread
import time

from agent_procutil import contained_test_mode, detached_kwargs, spawn_sync_in_kill_on_close_job

from . import engine_client as ec
from .companion_client import cancellable_read_runner
from .agenda_contract import bounded_json
from .engine_environment import _engine_environment
from .engine_posix_process import _OWNER
from .agenda_contract import AgendaUnavailable, MAX_FRAME_BYTES, _TRANSIENT_ERRORS

STARTUP_TIMEOUT = 5.0
IDLE_TIMEOUT = 5.0
_HEALTH_PROBE = """
import errno,json,sys
try:
    from agent_worktrees.worktrees_client import read_health
    health = read_health()
    result = {"ok": True, "health": health}
    raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 35 * 1024:
        raise ValueError("resident health exceeds its response bound")
except ImportError:
    raw = json.dumps({"ok": False, "error": {
        "code": "unsupported", "message": "owned resident health API is unsupported"}})
except (OSError, ValueError, RuntimeError, RecursionError) as error:
    if isinstance(error, PermissionError):
        code = "permission"
    elif isinstance(error, FileNotFoundError):
        code = "authentication"
    elif isinstance(error, TimeoutError):
        code = "timeout"
    elif isinstance(error, ConnectionError) or getattr(error,"errno",None) in {
        errno.ECONNREFUSED,errno.ECONNRESET,errno.ECONNABORTED,errno.ENETUNREACH,
        errno.ENETDOWN,errno.EHOSTUNREACH,errno.ETIMEDOUT,errno.EPIPE,
    }:
        code = "disconnected"
    elif isinstance(getattr(error,"code",None),str):
        code = error.code
    elif isinstance(error,(ValueError,RecursionError)):
        code = "malformed"
    else:
        code = "probe-failed"
    raw = json.dumps({"ok": False, "error": {
        "code": code, "message": str(error)[:1024] or type(error).__name__}})
sys.stdout.write(raw + "\\n")
sys.stdout.flush()
"""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("agenda frame contains a duplicate JSON member")
        result[key] = value
    return result


def _resolved_engine() -> list[str]:
    base = ec.engine_base_command()
    if not base or not Path(base[0]).is_absolute():
        raise AgendaUnavailable("no resolved absolute engine command is available")
    return base


def health_argv(base: list[str]) -> list[str]:
    """Read in the owned runtime, never dispatch an unknown CLI verb."""
    if len(base) < 3 or base[-2] != "-m" or base[-1] not in {
        "agent_worktrees", "agent_worktrees.current_roster_entry",
    }:
        raise AgendaUnavailable("resolved engine has no admitted read-only resident health probe")
    return [*base[:-2], "-I", "-c", _HEALTH_PROBE]


def require_capability(
    base: list[str], *, cancelled: Event, cwd: str,
    on_supported: Callable[[], None] | None = None,
) -> None:
    argv = health_argv(base)
    try:
        result = cancellable_read_runner(cancelled, cwd=cwd)(argv, STARTUP_TIMEOUT)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise AgendaUnavailable(f"resident health probe timed out: {exc}", retry=True, code="timeout") from exc
    except ConnectionError as exc:
        raise AgendaUnavailable(f"resident health probe disconnected: {exc}", retry=True, code="disconnected") from exc
    except OSError as exc:
        raise AgendaUnavailable(f"resident health probe failed: {exc}", code="probe-failed") from exc
    if cancelled.is_set():
        raise InterruptedError("agenda health probe cancelled")
    if (
        result.returncode != 0
        or len(result.stdout.encode("utf-8")) > MAX_FRAME_BYTES
        or len(result.stderr.encode("utf-8")) > 8192
    ):
        raise AgendaUnavailable("resident health probe failed or exceeded its response bound")
    value = json.loads(result.stdout, object_pairs_hook=_object)
    bounded_json(value)
    if not isinstance(value, dict):
        raise AgendaUnavailable("resident health probe returned a non-object response", code="malformed")
    if value.get("ok") is False:
        error = value.get("error")
        if (
            set(value) != {"ok", "error"} or not isinstance(error, dict)
            or set(error) != {"code", "message"}
            or not all(isinstance(error.get(key), str) and error[key] for key in ("code", "message"))
        ):
            raise AgendaUnavailable("resident health probe returned a malformed error", code="malformed")
        raise AgendaUnavailable(
            f"resident health is unavailable: {error['code']}: {error['message']}",
            retry=error["code"] in _TRANSIENT_ERRORS | {"timeout"}, code=error["code"],
        )
    if value.get("ok") is not True or set(value) != {"ok", "health"}:
        raise AgendaUnavailable("resident health probe returned invalid fields", code="malformed")
    health = value.get("health")
    if (
        not isinstance(health, dict) or not isinstance(health.get("capabilities"), list)
        or any(not isinstance(item, str) for item in health["capabilities"])
        or "worktree-agendas-v1" not in health["capabilities"]
    ):
        raise AgendaUnavailable("resident lacks worktree-agendas-v1; agenda command was not invoked")
    state = health.get("worktrees")
    if not isinstance(state, dict) or not isinstance(state.get("status"), str):
        raise AgendaUnavailable("resident health supplied an invalid authority state", code="malformed")
    generation = state.get("generation")
    if (
        not isinstance(generation, str) or len(generation) != 32
        or any(character not in "0123456789abcdef" for character in generation)
    ):
        raise AgendaUnavailable("resident health supplied an invalid generation")
    status = state["status"]
    if status != "ready" and status not in {
        "draining", "unready", "unavailable", "superseded", "closed", "shutdown",
    }:
        raise AgendaUnavailable("resident health supplied an unknown authority state", code="malformed")
    if on_supported is not None:
        on_supported()
    if status != "ready":
        raise AgendaUnavailable(
            f"resident agenda authority is {status}; no resident was started",
            retry=status in _TRANSIENT_ERRORS, code=status,
        )


def feed_argv(
    project: str, worktree: str, session: str, *, engine_command: list[str] | None = None,
) -> list[str]:
    base = _resolved_engine() if engine_command is None else engine_command
    return [
        *base, "agenda-feed", "--project", project, "--worktree", worktree,
        "--session", session, "--json",
    ]


@contextmanager
def _owned_process(argv: list[str], cwd: str):
    env = _engine_environment()
    # Reuse Manager's unreaped POSIX group-owner protocol; only its stdout
    # destination differs. This reserves the group identity through cleanup.
    if os.name != "nt" and not contained_test_mode():
        read_fd, write_fd = os.pipe()
        output = os.fdopen(read_fd, "rb")
        try:
            owner = subprocess.Popen(
                [sys.executable, "-B", "-c", _OWNER, json.dumps(argv), str(write_fd)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, cwd=cwd, start_new_session=True, pass_fds=(write_fd,),
            )
        except BaseException:
            output.close()
            raise
        finally:
            os.close(write_fd)
        try:
            yield output, owner.stderr
        finally:
            try:
                os.killpg(owner.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            owner.wait(timeout=5)
            owner.stdin.close()
            owner.stdout.close()
            owner.stderr.close()
            output.close()
    else:
        process, job = spawn_sync_in_kill_on_close_job(
            argv, require_job=os.name == "nt", allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
               else detached_kwargs()),
        )
        try:
            yield process.stdout, process.stderr
        finally:
            if job is not None:
                job.close()
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


def frames(
    project: str, worktree: str, session: str, *, cancelled: Event, cwd: str,
    on_supported: Callable[[], None] | None = None,
):
    """No route, token, filesystem-agenda scan, autostart or plugin imports."""
    if cancelled.is_set():
        return
    base = _resolved_engine()
    require_capability(base, cancelled=cancelled, cwd=cwd, on_supported=on_supported)
    if cancelled.is_set():
        return
    argv = feed_argv(project, worktree, session, engine_command=base)
    queue: Queue = Queue(maxsize=2)
    stopped = Event()
    errors = bytearray()

    def deliver(value):
        while not stopped.is_set():
            try:
                queue.put(value, timeout=0.1)
                return
            except Full:
                continue

    def read_lines(output):
        try:
            while not stopped.is_set():
                line = output.readline(MAX_FRAME_BYTES + 1)
                deliver(line)
                if not line or len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
                    return
        except OSError as exc:
            if not stopped.is_set():
                deliver(exc)

    def read_errors(output):
        try:
            while True:
                chunk = output.read(4096)
                if not chunk:
                    return
                errors.extend(chunk)
                del errors[:-8192]
        except OSError as exc:
            if not stopped.is_set():
                deliver(exc)

    readers = []
    try:
        with _owned_process(argv, cwd) as (stdout, stderr):
            readers = [
                Thread(target=read_lines, args=(stdout,), name="agenda-feed-stdout", daemon=True),
                Thread(target=read_errors, args=(stderr,), name="agenda-feed-stderr", daemon=True),
            ]
            for reader in readers:
                reader.start()
            deadline = time.monotonic() + STARTUP_TIMEOUT
            try:
                while not cancelled.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AgendaUnavailable("agenda feed missed its frame deadline", retry=True)
                    try:
                        line = queue.get(timeout=min(remaining, 0.1))
                    except Empty:
                        continue
                    if cancelled.is_set():
                        return
                    if isinstance(line, OSError):
                        raise line
                    if not line:
                        readers[1].join(timeout=0.1)
                        detail = bytes(errors).decode("utf-8", errors="replace").strip()
                        raise AgendaUnavailable(
                            detail or "agenda feed disconnected without a going-away notice",
                            retry=not detail,
                        )
                    if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
                        raise ValueError("agenda feed exceeded its atomic frame bound")
                    if time.monotonic() >= deadline:
                        raise AgendaUnavailable("agenda feed exceeded its frame deadline", retry=True)
                    frame = json.loads(line, object_pairs_hook=_object)
                    if not isinstance(frame, dict):
                        raise ValueError("agenda feed emitted a non-object frame")
                    yield frame
                    deadline = time.monotonic() + IDLE_TIMEOUT
            finally:
                stopped.set()
    except OSError as exc:
        raise AgendaUnavailable(f"could not read the owned agenda command: {exc}") from exc
    finally:
        stopped.set()
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            raise AgendaUnavailable("agenda pipe reader did not retire")
