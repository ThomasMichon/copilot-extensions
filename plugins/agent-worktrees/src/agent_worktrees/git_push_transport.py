"""Private push transport, shared by ancestry and proved owned-PR replay policies."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import git_ops, push_timeout


def push(
    remote: str, branch: str, *, cwd: str | Path,
    force_with_lease: bool = False, force_with_lease_expect: str | None = None,
    timeout: float | None = push_timeout.DEFAULT_PUSH_TIMEOUT,
) -> git_ops.PushResult:
    extra = ([f"--force-with-lease={branch.rsplit(':', 1)[-1]}:{force_with_lease_expect}"]
             if force_with_lease_expect is not None else
             ["--force-with-lease"] if force_with_lease else [])
    auth_args = git_ops._auth_config_args(remote, cwd=cwd)
    attempts = [auth_args, []] if auth_args else [[]]
    last_stderr = last_stdout = ""
    for prefix in attempts:
        try:
            result = git_ops.git(
                *prefix, "push", remote, branch, *extra, "--quiet",
                cwd=cwd, check=False, timeout=timeout, kill_tree=True,
            )
        except subprocess.TimeoutExpired as exc:
            return git_ops.PushResult(ok=False, stderr=push_timeout.message(exc, timeout))
        if result.returncode == 0:
            return git_ops.PushResult(ok=True)
        last_stderr, last_stdout = result.stderr or last_stderr, result.stdout or last_stdout
    return git_ops.PushResult(ok=False, stderr=last_stderr, stdout=last_stdout)
