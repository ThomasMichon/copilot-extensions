"""Update-run lifecycle: owner vs participant, ``events.jsonl``, phases, status.

Every public method fails open: a telemetry error prints one warning to
stderr and never raises into, or changes the result of, the update itself.
"""

from __future__ import annotations

import itertools
import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import outcomes as oc
from .fold import MAX_DETAIL, RUN_COMPONENT, fold
from .redact import redact, redact_argv

ENV_HOME = "COPILOT_EXTENSIONS_UPDATE_HOME"
ENV_RUN_ID = "COPILOT_EXTENSIONS_UPDATE_RUN_ID"
ENV_EVENTS = "COPILOT_EXTENSIONS_UPDATE_EVENTS"
ENV_OWNER = "COPILOT_EXTENSIONS_UPDATE_OWNER"
STATUS_FILE = "update-status.json"
STATUS_LOCK = "update-status.lock"
EVENTS_FILE = "events.jsonl"
KEEP_RUNS = 20
REFRESH_SECONDS = 30.0
STATUS_LOCK_SECONDS = 10.0
MAX_LINE = 8192
MAX_MESSAGE = 2048
ERROR_KEYS = frozenset({"code", "message", "os_errno", "winerror", "path", "op"})
MAX_DETAILS = 4096

_SEQ = itertools.count(1)
_WARNED = False


def _stderr(message: str) -> None:
    """Best-effort diagnostic: a closed or unencodable stderr is ignored."""
    try:
        print(message, file=sys.stderr)
    except Exception:  # noqa: S110 - a diagnostic must never escape
        pass


def _warn(exc: BaseException) -> None:
    global _WARNED
    if not _WARNED:
        _WARNED = True
        _stderr(f"warning: update telemetry unavailable: {exc}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def update_home(environ: MutableMapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = env.get(ENV_HOME)
    return Path(raw) if raw else Path.home() / ".copilot-extensions" / "update"


def new_run_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{stamp}-{os.getpid()}-{secrets.token_hex(2)}"


@contextmanager
def file_lock(path: Path, timeout: float) -> Iterator[None]:
    """Exclusive advisory lock on ``path`` (``msvcrt``/``fcntl``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise oc.LockTimeout(f"could not lock {path}") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def append_line(path: Path, line: str) -> None:
    """Append one record with a single ``write`` under a short lock."""
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    with file_lock(path.with_name(path.name + ".lock"), 5.0):
        # O_BINARY: without it Windows writes "\r\n", one byte past the line limit.
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def read_events(path: Path) -> tuple[list[dict], int]:
    """Parse ``events.jsonl``; unparseable lines are skipped and counted."""
    events: list[dict] = []
    errors = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return events, 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            errors += 1
            continue
        if isinstance(record, dict):
            events.append(record)
        else:
            errors += 1
    return events, errors


def write_json_atomic(path: Path, document: dict) -> None:
    """Temp file in the same directory, then ``os.replace`` (retried briefly)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _redact_obj(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(str(k)): _redact_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_obj(v) for v in value]
    return value


def _bound_strings(record: dict, limit: int) -> None:
    for key, value in list(record.items()):
        if isinstance(value, str) and len(value) > limit:
            record[key] = value[:limit]
        elif isinstance(value, dict):
            _bound_strings(value, limit)


def _fits(line: str) -> bool:
    """Whether ``line`` plus the newline ``append_line`` adds fits ``MAX_LINE``."""
    return len(line.encode("utf-8")) + 1 <= MAX_LINE


def encode_event(record: dict) -> str:
    """Serialize one event so the written line (newline included) is at most
    ``MAX_LINE`` UTF-8 bytes.

    An oversize record first loses ``argv`` and ``details``, then every
    remaining free-form string is bounded ever tighter until the line fits.
    """
    def dumps() -> str:
        return json.dumps(record, separators=(",", ":"), ensure_ascii=False)

    line = dumps()
    if _fits(line):
        return line
    record.pop("argv", None)
    if "details" in record:
        record["details"] = {"truncated": True}
    for limit in (512, 128, 32):
        _bound_strings(record, limit)
        line = dumps()
        if _fits(line):
            return line
    raise ValueError("update event exceeds the line limit")


class PhaseResult:
    """Mutable result a ``with run.phase(...)`` body may fill in."""

    def __init__(self) -> None:
        self.outcome: str | None = None
        self.changed: bool | None = None
        self.exit_code: int | None = None
        self.error: dict | None = None


class Run:
    """One update run as seen by this process (owner or participant)."""

    def __init__(self, *, run_id: str, events_path: Path | None, proc: str, owner: str,
                 is_owner: bool, trigger: str = "manual", home: Path | None = None,
                 enabled: bool = True) -> None:
        self.run_id = run_id
        self.events_path = events_path
        self.proc = proc
        self.owner = owner
        self.is_owner = is_owner
        self.trigger = trigger
        self.home = home
        self.enabled = enabled and events_path is not None
        self.started_at = utc_now()
        self._started = time.monotonic()
        self._last_status = 0.0
        self._own_required: list[str] = []
        self._open: dict[str, list[str]] = {}
        self._step: tuple[str, float] | None = None
        self._saved_env: dict[str, str | None] | None = None
        self._env: MutableMapping[str, str] | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    def from_env_or_new(cls, proc: str, trigger: str = "manual", *,
                        environ: MutableMapping[str, str] | None = None) -> Run:
        """Join the run named by the environment, or create and export one."""
        env = os.environ if environ is None else environ
        try:
            run_id, events = env.get(ENV_RUN_ID), env.get(ENV_EVENTS)
            if run_id and events and Path(events).is_absolute():
                return cls(run_id=run_id, events_path=Path(events), proc=proc,
                           owner=env.get(ENV_OWNER, ""), is_owner=False, trigger=trigger)
            home = update_home(env)
            run_id = new_run_id()
            run_dir = home / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            run = cls(run_id=run_id, events_path=(run_dir / EVENTS_FILE).resolve(), proc=proc,
                      owner=f"{proc}:{os.getpid()}", is_owner=True, trigger=trigger, home=home)
            run._export(env)
            _prune_runs(home / "runs", keep=KEEP_RUNS, current=run_id)
            run.emit("run", "run", "start")
            run._write_status(final=False)
            return run
        except Exception as exc:
            _warn(exc)
            return cls(run_id="", events_path=None, proc=proc, owner="", is_owner=False,
                       trigger=trigger, enabled=False)

    def _export(self, env: MutableMapping[str, str]) -> None:
        values = {ENV_RUN_ID: self.run_id, ENV_EVENTS: str(self.events_path),
                  ENV_OWNER: self.owner}
        self._env = env
        self._saved_env = {key: env.get(key) for key in values}
        env.update(values)

    def _restore_env(self) -> None:
        if self._env is None or self._saved_env is None:
            return
        for key, value in self._saved_env.items():
            if value is None:
                self._env.pop(key, None)
            else:
                self._env[key] = value
        self._saved_env = None

    # -- events --------------------------------------------------------------
    def emit(self, phase: str, component: str, event: str, **fields) -> None:
        """Append one event; ``None``-valued fields are omitted."""
        if not self.enabled:
            return
        try:
            record = {"v": 1, "ts": utc_now(), "run_id": self.run_id, "seq": next(_SEQ),
                      "pid": os.getpid(), "proc": self.proc, "phase": phase,
                      "component": component, "event": event}
            record.update({k: v for k, v in fields.items() if v is not None})
            if "argv" in record:
                record["argv"] = redact_argv(record["argv"])
            if "action" in record:
                record["action"] = redact(record["action"])
            if isinstance(record.get("error"), dict):
                error = _redact_obj(record["error"])
                # The published schema closes ``error``; drop anything else
                # (including a key the redactor masked) so every line validates.
                error = {k: v for k, v in error.items() if k in ERROR_KEYS}
                error["message"] = str(error.get("message", ""))[:MAX_MESSAGE]
                record["error"] = error
            if "details" in record:
                details = _redact_obj(record["details"])
                too_big = len(json.dumps(details)) > MAX_DETAILS
                record["details"] = {"truncated": True} if too_big else details
            line = encode_event(record)
            append_line(self.events_path, line)
            due = time.monotonic() - self._last_status >= REFRESH_SECONDS
            if self.is_owner and event == "end" and component == "run" and due:
                self._write_status(final=False)
        except Exception as exc:
            self._trip(exc)

    def _trip(self, exc: BaseException) -> None:
        """Per-run circuit breaker: after a persistence failure, stop writing
        so a broken store can't add a lock wait to every later event. The
        in-memory outcomes that decide the exit code are kept."""
        _warn(exc)
        self.enabled = False

    def _begin(self, phase: str, component: str, action: str | None, argv) -> float:
        if component == RUN_COMPONENT:
            self._open[phase] = []
        self.emit(phase, component, "start", action=action, argv=argv)
        return time.monotonic()

    def _end(self, phase: str, component: str, started: float, *, outcome: str | None,
             **fields) -> None:
        seen = self._open.pop(phase, []) if component == RUN_COMPONENT else []
        final = outcome if outcome == oc.SKIPPED and not seen else oc.worst([outcome, *seen])
        self.emit(phase, component, "end", outcome=final,
                  duration_ms=int((time.monotonic() - started) * 1000), **fields)

    @contextmanager
    def phase(self, phase: str, component: str = "run", action: str | None = None,
              argv=None) -> Iterator[PhaseResult]:
        """Record ``start``/``end`` around the body; exceptions map to outcomes."""
        result = PhaseResult()
        started = self._begin(phase, component, action, argv)
        try:
            yield result
        except BaseException as exc:
            outcome, error = oc.classify_exception(exc, op=action or phase)
            self._end(phase, component, started, outcome=outcome, error=error)
            raise
        self._end(phase, component, started, outcome=result.outcome, changed=result.changed,
                  exit_code=result.exit_code, error=result.error)

    def enter(self, phase: str, action: str | None = None) -> None:
        """Sequential phases: end the current one (if any) and start ``phase``."""
        self._close_step()
        self._step = (phase, self._begin(phase, RUN_COMPONENT, action, None))

    def _close_step(self, error: dict | None = None, outcome: str | None = None) -> None:
        if self._step is not None:
            phase, started = self._step
            self._step = None
            self._end(phase, RUN_COMPONENT, started, outcome=outcome, error=error)

    def target(self, phase: str, component: str, outcome: str, *, required: bool = True,
               changed: bool | None = None, detail: str | None = None,
               duration_ms: int | None = None, exit_code: int | None = None,
               error: dict | None = None, action: str | None = None) -> None:
        """Record one target's result (an ``end`` event for ``component``)."""
        if required:
            self._own_required.append(outcome)
            if phase in self._open:
                self._open[phase].append(outcome)
        details: dict = {"required": required}
        if detail:
            details["detail"] = redact(detail)[:MAX_DETAIL]
        self.emit(phase, component, "end", outcome=outcome, changed=changed,
                  duration_ms=duration_ms, exit_code=exit_code, error=error,
                  action=action, details=details)

    # -- completion ----------------------------------------------------------
    @property
    def outcome(self) -> str:
        """The most severe outcome over required targets this process recorded."""
        return oc.worst(self._own_required)

    def finish(self, outcome: str | None = None, *, error: dict | None = None) -> int:
        """Close the run; return the exit code for this process's result."""
        error_code = (error or {}).get("code")
        own = oc.worst([outcome, *self._own_required])
        code = oc.exit_code_for(own, error_code)
        try:
            self._close_step(error, outcome)
            if self.is_owner and self.enabled:
                self.emit("run", "run", "end", outcome=own, exit_code=code, error=error,
                          duration_ms=int((time.monotonic() - self._started) * 1000))
                self._write_status(final=True, hint=own, error_code=error_code)
        except Exception as exc:
            _warn(exc)
        finally:
            self._restore_env()
        return code

    def _write_status(self, *, final: bool, hint: str | None = None,
                      error_code: str | None = None) -> None:
        if not (self.is_owner and self.enabled and self.home is not None):
            return
        try:
            self._last_status = time.monotonic()
            events, parse_errors = read_events(self.events_path)
            now = utc_now()
            meta = {
                "id": self.run_id, "owner": self.owner, "trigger": self.trigger,
                "started_at": self.started_at, "ended_at": now if final else None,
                "duration_ms": int((time.monotonic() - self._started) * 1000),
                "host": socket.gethostname(), "platform": sys.platform,
                "events_path": str(self.events_path), "event_parse_errors": parse_errors,
            }
            status_path = self.home / STATUS_FILE
            with file_lock(self.home / STATUS_LOCK, STATUS_LOCK_SECONDS):
                try:
                    previous = json.loads(status_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    previous = None
                document = fold(events, run=meta, previous=previous, final=final,
                                hint=hint, error_code=error_code, updated_at=now)
                newer = _newer_run(previous, self.run_id, self.started_at)
                if newer:
                    _stderr(f"warning: update status left to newer run {newer}")
                    document = _merge_completion(previous, document, now) if final else None
                if document is not None:
                    write_json_atomic(status_path, document)
        except Exception as exc:
            self._trip(exc)


def _newer_run(previous, run_id: str, started_at: str) -> str | None:
    """The id of the run recorded in ``previous`` if it started after this one.

    Runs are ordered by ``run.started_at`` (UTC, fixed-width ISO text), then by
    run id, so an older overlapping run never overwrites a newer run's status.
    """
    run = previous.get("run") if isinstance(previous, dict) else None
    if not isinstance(run, dict) or not isinstance(run.get("id"), str):
        return None
    other_id, other_start = run["id"], str(run.get("started_at") or "")
    if other_id == run_id:
        return None
    return other_id if (other_start, other_id) > (started_at, run_id) else None


def _merge_completion(previous: dict, document: dict, now: str) -> dict | None:
    """Keep a newer run's status, carrying an older run's later completion.

    The older run's ``last_success``/``last_failure`` replaces the recorded one
    only when it ended later; ``None`` means nothing needs writing.
    """
    merged, changed = dict(previous), False
    for key in ("last_success", "last_failure"):
        mine, recorded = document.get(key), previous.get(key)
        if not isinstance(mine, dict) or mine.get("run_id") != document["run"]["id"]:
            continue
        if isinstance(recorded, dict) and str(recorded.get("ended_at") or "") >= str(
            mine.get("ended_at") or ""
        ):
            continue
        merged[key], changed = mine, True
    if changed:
        merged["updated_at"] = now
    return merged if changed else None


def _prune_runs(runs_dir: Path, *, keep: int, current: str) -> None:
    try:
        names = sorted(p.name for p in runs_dir.iterdir() if p.is_dir() and p.name != current)
    except OSError:
        return
    for name in names[: max(0, len(names) - (keep - 1))]:
        shutil.rmtree(runs_dir / name, ignore_errors=True)
