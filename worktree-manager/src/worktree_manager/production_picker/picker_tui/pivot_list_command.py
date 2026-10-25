"""One-shot provider reads with bounded, caller-owned descendant cleanup."""
from __future__ import annotations

import json
import subprocess

from agent_procutil.owned_process import run_caller_owned

from ... import picker_review


def exec_list(runtime, context):
    from . import tasks

    argv = tasks.format_template(runtime.pivot.list_cmd, context)
    if not argv:
        return "error", [], "empty list command", {}
    try:
        environment = tasks._child_process_env()
        if picker_review.active():
            argv, binding = picker_review.list_command(runtime.pivot, argv)
            environment.update(binding)
            result = picker_review.run_read(
                argv, capture_output=True, text=True, timeout=tasks.LIST_TIMEOUT,
                check=False, env=environment, creationflags=tasks.no_window_flags(),
            )
        else:
            argv = tasks._resolve_command(argv)
            result = run_caller_owned(
                argv, timeout=tasks.LIST_TIMEOUT, env=environment,
                max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=256 * 1024,
            )
    except FileNotFoundError:
        return "error", [], f"{argv[0]} not found on PATH", {}
    except subprocess.TimeoutExpired as error:
        return (
            "error", [],
            f"{runtime.pivot.label} list timed out after {error.timeout:g}s; press r to retry this scope", {},
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        return "error", [], f"{type(error).__name__}: {str(error).strip()[:160]}", {}
    if result.returncode:
        detail = (result.stderr or "").strip().splitlines()
        return "error", [], (detail[-1] if detail else f"exit {result.returncode}")[:200], {}
    try:
        payload = json.loads(result.stdout or "[]")
    except ValueError:
        return "error", [], "list command did not print JSON", {}
    rows, summary = tasks.parse_list_payload(payload)
    return "ready", rows, "", summary
