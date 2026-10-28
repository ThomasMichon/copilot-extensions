"""Atomic, process-identified admission for bounded guidance backup workers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from agent_procutil import windowless_daemon_kwargs
from single_instance_lease import AlreadyRunningError, SingleInstance, read_owner_pid

from . import activity, locks

_LOG = logging.getLogger(__name__)
_MAX_STATE_BYTES = 256 * 1024
_MAX_LOG_BYTES = 8192


def _owner(pid: int) -> dict:
    start = locks.process_start_time(pid)
    if start is None:
        raise OSError(f"cannot verify guidance worker process {pid}")
    return {"pid": pid, "start_time": start}


def _read(path: Path) -> dict | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > _MAX_STATE_BYTES:
        raise ValueError("guidance worker state exceeds its read limit")
    data = json.loads(raw)
    if (
        not isinstance(data, dict) or data.get("version") != 1
        or not isinstance(data.get("request_id"), str)
        or not isinstance(data.get("root"), str)
        or data.get("phase") not in ("spawning", "queued", "running", "done", "failed")
        or not isinstance(data.get("owner"), dict)
        or type(data["owner"].get("pid")) is not int
        or not isinstance(data["owner"].get("start_time"), str)
    ):
        raise ValueError("invalid guidance worker state")
    return data


def _write(path: Path, text: str) -> None:
    from . import tracking

    descriptor, name = tempfile.mkstemp(prefix=".guidance-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tracking._replace_with_retry(str(staged), str(path), attempts=5, delay=0.02)
    finally:
        staged.unlink(missing_ok=True)


def _save(directory: Path, state: dict) -> None:
    _write(directory / "request.json", json.dumps(state) + "\n")


def _log(directory: Path, diagnostic: str) -> None:
    raw = (diagnostic + "\n").encode("utf-8")
    if len(raw) > _MAX_LOG_BYTES:
        suffix = b"\n[truncated]\n"
        raw = raw[:_MAX_LOG_BYTES - len(suffix)].decode("utf-8", errors="ignore").encode("utf-8") + suffix
    _write(directory / "refresh.log", raw.decode("utf-8"))


def _gate(directory: Path) -> SingleInstance:
    return SingleInstance(directory, service="local-guidance")


def _failed(state: dict, root: Path) -> None:
    from .static_preparation import publication_revision

    state.update(
        phase="failed", completed_at=time.time(),
        result={
            "status": "failed", "model_admission": "unknown", "counts_known": False,
            "publication_revision": publication_revision(root),
        },
    )


def _reap(process: subprocess.Popen, root: Path, directory: Path, request_id: str) -> None:
    code = process.wait()
    if code:
        activity.log_event("local_guidance_worker_failed", root=str(root), exit_code=code)
        gate = _gate(directory)
        try:
            if not directory.is_dir():
                return
            gate.acquire()
            state = _read(directory / "request.json")
            terminal = state and (
                state["phase"] == "done" or (
                    state["phase"] == "failed" and isinstance(state.get("result"), dict)
                    and isinstance(state.get("completed_at"), (int, float))
                )
            )
            if state and state["request_id"] == request_id and not terminal:
                _failed(state, root)
                _log(directory, f"[local-guidance] failed: worker exited {code} before publication")
                _save(directory, state)
        except (OSError, ValueError, AlreadyRunningError) as exc:
            _LOG.warning("Could not record failed guidance worker: %s", exc)
        finally:
            gate.release()


def queue(root: Path, directory: Path):
    from . import status_monitor_runtime
    from .local_cache_refresh import RefreshResult

    directory.mkdir(exist_ok=True)
    gate = _gate(directory)
    try:
        gate.acquire()
    except AlreadyRunningError:
        return RefreshResult(
            "failed", blocking=1,
            detail="guidance dispatch reserved; request not queued; retry at the next lifecycle boundary",
        )
    try:
        state = _read(directory / "request.json")
        if state and state["phase"] not in ("done", "failed") and locks.lock_is_live(state["owner"]):
            state["pending_rerun"] = True
            _save(directory, state)
            return RefreshResult("queued", detail=f"refresh already active; not yet delivered; result: {directory / 'refresh.log'}")
        request_id = uuid.uuid4().hex
        state = {
            "version": 1, "root": str(root), "request_id": request_id,
            "phase": "spawning", "owner": _owner(os.getpid()),
        }
        _save(directory, state)
        _log(directory, "[local-guidance] queued: not yet delivered")
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_worktrees.local_cache_worker",
                 str(root), str(directory), request_id],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=status_monitor_runtime._daemon_cwd(),
                env=status_monitor_runtime._daemon_environment(),
                **windowless_daemon_kwargs(breakaway=True),
            )
            state.update(phase="queued", owner=_owner(process.pid))
            _save(directory, state)
        except (OSError, ValueError) as exc:
            _failed(state, root)
            _log(directory, f"[local-guidance] failed: dispatch failed: {exc}")
            _save(directory, state)
            if process is not None:
                threading.Thread(target=_reap, args=(process, root, directory, request_id), daemon=True).start()
            raise
    finally:
        gate.release()
    threading.Thread(target=_reap, args=(process, root, directory, request_id), daemon=True).start()
    return RefreshResult("queued", detail=f"background refresh pid {process.pid}; not yet delivered; result: {directory / 'refresh.log'}")


def run(root: Path, directory: Path, request_id: str | None = None) -> int:
    from .local_cache_refresh import DEFAULT_TIMEOUT_S, RefreshResult, refresh_local_cache

    gate = _gate(directory)
    deadline = time.monotonic() + 5.0
    while True:
        try:
            if not directory.is_dir():
                raise OSError("guidance workspace metadata was removed")
            gate.acquire()
            break
        except AlreadyRunningError:
            if request_id is None or time.monotonic() >= deadline:
                print(RefreshResult("skipped", detail="another refresh worker owns this repository").diagnostic)
                return 0
            time.sleep(0.01)
    try:
        state = _read(directory / "request.json")
        if request_id is None:
            if state and state["phase"] not in ("done", "failed") and locks.lock_is_live(state["owner"]):
                print(RefreshResult("skipped", detail="another refresh worker owns this repository").diagnostic)
                return 0
            request_id = uuid.uuid4().hex
            state = {"version": 1, "root": str(root), "request_id": request_id}
        elif (
            state is None or state["request_id"] != request_id or state["root"] != str(root)
            or state["phase"] != "queued"
            or state["owner"]["pid"] not in (os.getpid(), os.getppid())
            or not locks.lock_is_live(state["owner"])
        ):
            raise ValueError("guidance worker admission was not confirmed")
        state.update(phase="running", owner=_owner(os.getpid()), pending_rerun=False)
        _save(directory, state)
    finally:
        gate.release()
    copy_deadline = time.monotonic() + DEFAULT_TIMEOUT_S
    restart_required = False
    while True:
        result = refresh_local_cache(root, timeout=max(0.0, copy_deadline - time.monotonic()))
        restart_required |= bool(result.changed or result.removed)
        try:
            if not directory.is_dir():
                raise OSError("guidance workspace metadata was removed")
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    gate.acquire()
                    break
                except AlreadyRunningError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            current = _read(directory / "request.json")
            if current is None or current["request_id"] != request_id:
                raise ValueError("guidance worker ownership changed before completion")
            if current.get("pending_rerun"):
                if time.monotonic() < copy_deadline:
                    current["pending_rerun"] = False
                    _save(directory, current)
                    continue
                result = replace(
                    result, status="timeout", blocking=result.blocking + 1,
                    detail="backup refresh budget exhausted with another copy requested",
                )
            _log(directory, result.diagnostic)
            current.update(
                phase="done" if result.status in ("ready", "partial", "skipped") else "failed",
                completed_at=time.time(),
                result={
                    "status": result.status, "installed": result.changed if result.counts_known else None,
                    "unchanged": result.unchanged if result.counts_known else None,
                    "removed": result.removed if result.counts_known else None,
                    "counts_known": result.counts_known,
                    "generation": result.generation, "model_admission": "unknown",
                    "publication_revision": result.publication_revision,
                    "restart_required": restart_required or (None if not result.counts_known else False),
                },
            )
            _save(directory, current)
            break
        finally:
            gate.release()
    print(result.diagnostic, flush=True)
    return 0 if result.status in ("ready", "skipped") else 1


def metadata_directory(root: str | Path) -> Path | None:
    """Resolve checkout-local metadata without spawning Git inside a hook."""
    root = Path(root)
    marker = root / ".git"
    if marker.is_dir():
        git_dir = marker
    elif marker.is_file():
        text = marker.read_text(encoding="utf-8")
        if not text.startswith("gitdir:"):
            raise ValueError("invalid Git administrative pointer")
        target = Path(text[len("gitdir:"):].strip())
        git_dir = target if target.is_absolute() else root / target
    elif (root / "HEAD").is_file() and (root / "objects").is_dir():
        git_dir = root
    else:
        return None
    return git_dir.resolve() / "agent-worktrees-local-guidance"


def watch_completion(
    root: Path, session_id: str | None, *, prepared: bool = False, initial_revision: str = "",
    started_at: float | None = None,
) -> None:
    """A hook-owned watch lets the next post-tool hook surface late completion."""
    import re

    if not session_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        return
    directory = Path.home() / ".copilot" / "session-state" / session_id
    if not directory.is_dir() or directory.is_symlink():
        return
    path = directory / "local-guidance-watch.json"
    if prepared:
        path.unlink(missing_ok=True)
        return
    metadata = metadata_directory(root)
    if metadata is not None:
        _write(path, json.dumps({
            "version": 1, "root": str(root), "metadata": str(metadata),
            "started_at": time.time() if started_at is None else started_at,
            "publication_revision": initial_revision,
        }) + "\n")


def is_active(root: str | Path) -> bool:
    """Cheap workspace-local liveness, including the queued handoff interval."""
    root = Path(root)
    try:
        directory = metadata_directory(root)
        if directory is None or not directory.is_dir():
            return False
        state = _read(directory / "request.json")
        if state is not None:
            if os.path.normcase(state["root"]) != os.path.normcase(str(root.resolve())):
                raise ValueError("guidance worker workspace identity mismatch")
            return state["phase"] != "failed" and locks.lock_is_live(state["owner"])
        lock_path = directory / "local-guidance.lock"
        try:
            lock_path.lstat()
        except FileNotFoundError:
            return False
        owner = read_owner_pid(lock_path)
        return locks.pid_alive(owner) if owner else True
    except (OSError, ValueError) as exc:
        _LOG.warning("Guidance worker liveness is unconfirmed: %s", exc)
        return True


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Expected checkout, metadata directory, and request identity")
    from .local_cache_refresh import _worker_directory

    root, directory = Path(sys.argv[1]), Path(sys.argv[2])
    if _worker_directory(root) != directory:
        raise SystemExit("Guidance worker metadata identity mismatch")
    raise SystemExit(run(root, directory, sys.argv[3]))
