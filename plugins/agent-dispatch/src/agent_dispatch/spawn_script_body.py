"""The script-backed spawn body: handle encode/decode, liveness, spec
loading, and the ``make_script_spawn`` factory itself.

Split out of :mod:`agent_dispatch.spawn_factories` (itself split out of
:mod:`agent_dispatch.supervisor` -- see that module's own docstring for the
invariant these functions serve) purely for module size: the script-body
family is a cohesive, self-contained concern (a plain deterministic
subprocess embodiment, distinct from the embody/headless agent-session
factories) with no shared mutable state. Every name here is re-exported
unchanged from ``spawn_factories.py``, so a plain *import* of any of them
through ``agent_dispatch.spawn_factories.<name>`` is unaffected -- but
re-exporting a function does not change its own defining module's globals,
so a test that *monkeypatches* one of this module's globals (e.g.
``subprocess``, used by ``make_script_spawn``'s own ``Popen`` call) must
patch it on ``agent_dispatch.spawn_script_body`` directly, not on
``spawn_factories``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

SpawnFn = Callable[[dict], "tuple[bool, dict]"]
ScriptBodyVerdictFn = Callable[[int, "str | None"], str]

_SCRIPT_BODY_PREFIX = "script-body:"


def _cleanup_script_task_file(task_file: str | None) -> None:
    if not isinstance(task_file, str) or not task_file:
        return
    try:
        Path(task_file).unlink(missing_ok=True)
    except OSError:
        pass


def _encode_script_body_handle(
    worker_id: str,
    pid: int,
    start_token: str | None,
    *,
    task_file: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "worker_id": worker_id,
            "pid": pid,
            "start_token": start_token,
            "task_file": task_file,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{_SCRIPT_BODY_PREFIX}{payload}"


def _parse_script_body_handle(
    session_handle: str | None,
) -> tuple[str, int, str | None, str | None] | None:
    """Decode a ``script-body:<json>`` reservation handle.

    Returns ``(worker_id, pid, start_token, task_file)`` for a script body embodied on this
    host, else ``None``.
    """
    if not session_handle or not session_handle.startswith(_SCRIPT_BODY_PREFIX):
        return None
    raw = session_handle[len(_SCRIPT_BODY_PREFIX) :]
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    worker_id = payload.get("worker_id")
    pid = payload.get("pid")
    start_token = payload.get("start_token")
    task_file = payload.get("task_file")
    if not isinstance(worker_id, str) or not worker_id:
        return None
    if not isinstance(pid, int) or pid <= 0:
        return None
    if start_token is not None and not isinstance(start_token, str):
        return None
    if task_file is not None and not isinstance(task_file, str):
        return None
    return worker_id, pid, start_token, task_file


def _default_script_body_verdict(pid: int, start_token: str | None) -> str:
    """Resolve a local script body's liveness to a tri-state verdict.

    PID reuse is fenced with ``start_token`` when available. A missing process is
    ``gone``; a running process with a matching start token is ``live``; any probe
    uncertainty is ``unknown`` rather than a false death.
    """
    from . import companion

    try:
        if not companion._process_exists(pid):
            return _tracking().GONE
    except Exception:
        return _tracking().UNKNOWN
    if not start_token:
        return _tracking().LIVE
    try:
        current = companion.process_start_token(pid)
    except Exception:
        return _tracking().UNKNOWN
    if current is None:
        return _tracking().UNKNOWN
    return _tracking().LIVE if current == start_token else _tracking().GONE


def _load_script_spec(task: dict) -> tuple[list[str], str | None, dict[str, str], int | None]:
    raw_payload = task.get("payload_inline")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        raise ValueError("script embodiment requires a JSON payload_inline object")
    try:
        payload = json.loads(raw_payload)
    except ValueError as exc:
        raise ValueError("script payload_inline is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("script payload_inline must be a JSON object")
    spec = payload.get("script") if isinstance(payload.get("script"), dict) else payload
    if not isinstance(spec, dict):
        raise ValueError("script payload must be an object or carry a 'script' object")

    argv_value = spec.get("argv")
    path_value = spec.get("path")
    args_value = spec.get("args") or []
    cwd_value = spec.get("cwd")
    env_value = spec.get("env") or {}
    heartbeat_value = spec.get("heartbeat_seconds")

    if path_value is not None:
        if argv_value is not None:
            raise ValueError("script payload cannot specify both 'path' and 'argv'")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("script payload 'path' must be a non-empty string")
        if not isinstance(args_value, list) or not all(
            isinstance(item, str) and item for item in args_value
        ):
            raise ValueError("script payload 'args' must be a list of non-empty strings")
        script_path = Path(path_value).expanduser()
        if not script_path.is_absolute():
            raise ValueError("script payload 'path' must be absolute")
        if not script_path.is_file():
            raise ValueError(f"script payload path does not exist: {script_path}")
        from .procutil import resolve_own_runtime_python

        argv = [resolve_own_runtime_python(), str(script_path), *args_value]
    else:
        if not isinstance(argv_value, list) or not argv_value:
            raise ValueError("script payload must provide either 'path' or a non-empty 'argv'")
        if not all(isinstance(item, str) and item for item in argv_value):
            raise ValueError("script payload 'argv' must be a list of non-empty strings")
        argv = list(argv_value)

    cwd: str | None = None
    if cwd_value is not None:
        if not isinstance(cwd_value, str) or not cwd_value.strip():
            raise ValueError("script payload 'cwd' must be a non-empty string when provided")
        cwd_path = Path(cwd_value).expanduser()
        if not cwd_path.is_absolute():
            raise ValueError("script payload 'cwd' must be absolute")
        cwd = str(cwd_path)

    if not isinstance(env_value, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in env_value.items()
    ):
        raise ValueError("script payload 'env' must be an object of string:string pairs")
    env = dict(env_value)

    heartbeat_seconds: int | None = None
    if heartbeat_value is not None:
        if not isinstance(heartbeat_value, int) or heartbeat_value <= 0:
            raise ValueError("script payload 'heartbeat_seconds' must be a positive integer")
        heartbeat_seconds = heartbeat_value

    return argv, cwd, env, heartbeat_seconds


def _tracking():
    """Lazy accessor for the ``tracking`` module (its verdict constants)."""
    from . import tracking

    return tracking


def make_script_spawn(
    *,
    route: str = "",
    all_repos: bool = False,
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker as a plain deterministic
    subprocess rather than an agent session.

    The task's ``payload_inline`` must be JSON describing the command to run:

    - ``{"path": "C:\\absolute\\worker.py", "args": [...], "cwd": "...", "env": {...}}``
      runs the file with this plugin's own runtime Python; or
    - ``{"argv": ["python-or-exe", "..."], "cwd": "...", "env": {...}}``
      runs the exact argv directly.

    The spawned process receives task/coordinator context via environment
    variables so it can drive the ordinary claim/start/progress/complete/
    abandon lifecycle through :mod:`agent_dispatch.script_worker` without ever
    invoking an LLM.
    """

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"script-{uuid.uuid4().hex[:8]}"
        try:
            argv, cwd, extra_env, heartbeat_seconds = _load_script_spec(task)
        except ValueError as exc:
            return False, {"error": str(exc)}

        task_file: tempfile.NamedTemporaryFile[str] | None = None
        task_file_path: str | None = None
        try:
            task_file = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=f"-{task.get('id') or 'task'}.json",
                delete=False,
            )
            json.dump(task, task_file, sort_keys=True)
            task_file.flush()
            task_file_path = task_file.name
            task_file.close()

            env = dict(os.environ)
            env.update(extra_env)
            env.update(
                {
                    "AGENT_DISPATCH_SCRIPT_TASK_ID": str(task["id"]),
                    "AGENT_DISPATCH_SCRIPT_WORKER_ID": worker_id,
                    "AGENT_DISPATCH_SCRIPT_ROUTE": (
                        "shared" if route.strip() == "--shared" else "local"
                    ),
                    "AGENT_DISPATCH_SCRIPT_REPO": (
                        "" if all_repos else str(task.get("repo") or "")
                    ),
                    "AGENT_DISPATCH_SCRIPT_ALL_REPOS": "1" if all_repos else "0",
                    "AGENT_DISPATCH_SCRIPT_TASK_FILE": task_file_path,
                }
            )
            if heartbeat_seconds is not None:
                env["AGENT_DISPATCH_SCRIPT_HEARTBEAT_SECONDS"] = str(heartbeat_seconds)

            from .procutil import _process_tree_kwargs

            process = subprocess.Popen(  # noqa: S603 -- deterministic argv from task payload
                argv,
                cwd=cwd or None,
                env=env,
                stdin=subprocess.DEVNULL,
                **_process_tree_kwargs(),
            )
        except OSError as exc:
            if task_file_path:
                Path(task_file_path).unlink(missing_ok=True)
            return False, {"error": str(exc)}
        start_token = None
        try:
            from . import companion

            start_token = companion.process_start_token(process.pid)
        except Exception:
            start_token = None
        return True, {
            "session": _encode_script_body_handle(
                worker_id,
                process.pid,
                start_token,
                task_file=task_file_path,
            ),
            "worktree": None,
        }

    spawn.requires_reusable_worktree = False
    spawn.allocation_driver = "agent-dispatch"
    spawn.allocation_interface = "script"
    return spawn
