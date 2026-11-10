"""Opt-in bounded provider capture through the existing caller-owned process primitive."""

import subprocess

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned


def run_bounded(
    argv, sanitized, *, env, input_text, timeout, max_stdout_bytes, max_stderr_bytes,
):
    try:
        result = run_caller_owned(
            argv, env=env, input_text=input_text, timeout=timeout,
            max_stdout_bytes=max_stdout_bytes, max_stderr_bytes=max_stderr_bytes,
        )
        return subprocess.CompletedProcess(
            sanitized, result.returncode, result.stdout, result.stderr,
        )
    except OwnedCleanupError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(
            sanitized, 124, "", "bounded provider read unavailable or exceeded capture budget",
        )
