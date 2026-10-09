"""Best-effort squash-invariant metadata, not replay authorization."""

from __future__ import annotations

import subprocess

from . import git_ops


def _patch_id(base: str, head: str, *, cwd: str) -> str:
    """Squash-invariant patch-id of ``base..head`` (#898), or "" on failure.

    ``git diff base..head | git patch-id --stable`` identifies the *change
    content*, invariant across a squash / rebase / re-commit -- so a downstream
    recorder (an issue-close comment, a session ref) can bind to something that
    survives the server-side squash-merge rewriting the commit SHA, instead of a
    pre-squash SHA that dangles once the work lands. Best-effort: an empty diff or
    any git error yields "".
    """
    if not base:
        return ""
    diff = git_ops.git("diff", f"{base}..{head}", cwd=cwd, check=False)
    if diff.returncode != 0 or not diff.stdout:
        return ""
    try:
        pid = subprocess.run(
            ["git", "patch-id", "--stable"],
            input=diff.stdout, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if pid.returncode != 0:
        return ""
    out = pid.stdout.strip()
    return out.split()[0] if out else ""


def _commit_patch_ids(base: str, head: str, *, cwd: str) -> dict[str, set[str]]:
    """Map patch IDs to non-merge commits through one streaming Git pipeline."""
    if not base:
        return {}
    log_process: subprocess.Popen[bytes] | None = None
    patch_process: subprocess.Popen[str] | None = None
    env = git_ops.repository_identity_env()
    try:
        log_process = subprocess.Popen(
            ["git", "log", "--no-merges", "--format=commit %H", "-p", f"{base}..{head}"],
            cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if log_process.stdout is None:
            log_process.kill()
            log_process.wait()
            return {}
        patch_process = subprocess.Popen(
            ["git", "patch-id", "--stable"],
            cwd=cwd, env=env, stdin=log_process.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        )
        log_process.stdout.close()
        output, _ = patch_process.communicate(timeout=30)
        log_returncode = log_process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        if patch_process is not None and patch_process.poll() is None:
            patch_process.kill()
            patch_process.communicate()
        if log_process is not None and log_process.poll() is None:
            log_process.kill()
            log_process.wait()
        return {}
    if patch_process.returncode != 0 or log_returncode != 0:
        return {}
    result: dict[str, set[str]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result.setdefault(parts[0], set()).add(parts[1])
    return result
