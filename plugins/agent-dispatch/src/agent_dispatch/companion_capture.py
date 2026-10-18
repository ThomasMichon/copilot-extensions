"""Captured companion commands bound execution and post-retirement pipe draining."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping

from . import companion as lifecycle


def _run_captured(
    command: tuple[str, ...], *, cwd: str, environment: Mapping[str, str],
    timeout: float, input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + timeout
    kwargs = lifecycle.no_window_kwargs() if os.name == "nt" else lifecycle.detached_kwargs()
    try:
        process = subprocess.Popen(  # noqa: S603 -- attributed fixed argv
            list(command),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=dict(environment), **kwargs,
        )
        try:
            stdout, stderr = process.communicate(
                input_text, timeout=max(0.0, deadline - time.monotonic()),
            )
        except subprocess.TimeoutExpired as exc:
            if lifecycle.contained_test_mode():
                process.kill()
            elif os.name == "nt":
                lifecycle._terminate_windows_tree(process.pid)
            else:
                lifecycle._terminate_posix_group(process.pid, grace=1.0)
            try:
                process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                # Windows reader threads may still own descendant-inherited pipes.
                # Closing a TextIO stream here can itself block waiting for them.
                pass
            process.poll()
            raise lifecycle.CompanionIndeterminate("companion command timed out") from exc
        return subprocess.CompletedProcess(
            command, process.returncode, stdout=stdout, stderr=stderr,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise lifecycle.CompanionIndeterminate("companion command unavailable") from exc
