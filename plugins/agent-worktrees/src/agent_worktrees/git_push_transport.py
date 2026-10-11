"""Private push transport, shared by ancestry and proved owned-PR replay policies."""

from __future__ import annotations

import subprocess
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import git_ops, push_timeout

_destination: ContextVar[tuple[str, str] | None] = ContextVar("push_destination", default=None)


@contextmanager
def pinned_destination(remote: str, url: str):
    """Bind this publication's transport/auth to an already verified URL."""
    token = _destination.set((remote, url))
    try:
        yield
    finally:
        _destination.reset(token)


def push(
    remote: str, branch: str, *, cwd: str | Path,
    force_with_lease: bool = False, force_with_lease_expect: str | None = None,
    timeout: float | None = push_timeout.DEFAULT_PUSH_TIMEOUT,
) -> git_ops.PushResult:
    extra = ([f"--force-with-lease={branch.rsplit(':', 1)[-1]}:{force_with_lease_expect}"]
             if force_with_lease_expect is not None else
             ["--force-with-lease"] if force_with_lease else [])
    pinned = _destination.get()
    source, separator, target = branch.partition(":")
    if pinned and pinned[0] == remote:
        sha = git_ops.git("rev-parse", "--verify", f"{source}^{{commit}}", cwd=cwd,
                          isolated_repository=True).stdout.strip()
        target = target if separator else "refs/heads/" + source.removeprefix("refs/heads/")
        branch = f"{sha}:{target}"
    destination = pinned[1] if pinned and pinned[0] == remote else remote
    transport_remote = remote
    destination_args = ([
        "-c", f"remote.{transport_remote}.pushurl=",
        "-c", f"remote.{transport_remote}.pushurl={destination}",
        "-c", f"url.{destination}.insteadOf={destination}",
    ] if pinned and pinned[0] == remote else [])
    auth_args = (git_ops._auth_config_args_for_url(destination) if pinned and pinned[0] == remote
                 else git_ops._auth_config_args(remote, cwd=cwd))
    attempts = [auth_args, []] if auth_args else [[]]
    last_stderr = last_stdout = ""
    for prefix in attempts:
        try:
            result = git_ops.git(
                *prefix, *destination_args, "push", transport_remote, branch, *extra, "--quiet",
                cwd=cwd, check=False, timeout=timeout, kill_tree=True,
                isolated_repository=True,
            )
        except subprocess.TimeoutExpired as exc:
            return git_ops.PushResult(ok=False, stderr=push_timeout.message(exc, timeout))
        if result.returncode == 0:
            warning = ""
            if pinned and pinned[0] == remote:
                try:
                    _refresh_tracking(remote, destination, branch, cwd, timeout,
                                      force_with_lease_expect)
                except (OSError, git_ops.GitError, TimeoutError) as exc:
                    warning = f"Push succeeded, but local remote-tracking refresh failed: {type(exc).__name__}."
                    logging.getLogger("agent-worktrees").warning(warning)
            return git_ops.PushResult(ok=True, stderr=warning)
        last_stderr, last_stdout = result.stderr or last_stderr, result.stdout or last_stdout
    return git_ops.PushResult(ok=False, stderr=last_stderr, stdout=last_stdout)


def _refresh_tracking(remote: str, destination: str, refspec: str, cwd: str | Path,
                      timeout: float | None, expected: str | None = None) -> None:
    """Preserve Git's named-remote tracking update only for the still-matching remote."""
    from . import pr_publish

    source, separator, target = refspec.partition(":")
    if not separator:
        source = refspec
        target = "refs/heads/" + refspec.removeprefix("refs/heads/")
    if not target.startswith("refs/heads/"):
        return
    with pr_publish.publish_lock(str(cwd), push_timeout_seconds=timeout or push_timeout.DEFAULT_PUSH_TIMEOUT):
        current = git_ops.git("remote", "get-url", "--push", "--all", remote,
                              cwd=cwd, check=False)
        if current.returncode or current.stdout.strip() != destination:
            return
        mappings = git_ops.git("config", "--get-all", f"remote.{remote}.fetch",
                               cwd=cwd, check=False).stdout.splitlines()
        for mapping in mappings:
            fetch_source, _, fetch_target = mapping.lstrip("+").partition(":")
            if fetch_source == target and fetch_target:
                tracked = fetch_target
            elif fetch_source == "refs/heads/*" and fetch_target.endswith("/*"):
                tracked = fetch_target[:-1] + target.removeprefix("refs/heads/")
            else:
                continue
            sha = git_ops.git("rev-parse", "--verify", source, cwd=cwd).stdout.strip()
            previous = git_ops.git("rev-parse", "--verify", tracked, cwd=cwd,
                                   check=False).stdout.strip()
            if previous and previous != expected and not git_ops.is_commit_ancestor(previous, sha, cwd=cwd):
                # Another completed publication may already have advanced this
                # tracking ref. Never roll it back to an older successful push.
                return
            git_ops.git("update-ref", tracked, sha, previous or "0" * len(sha), cwd=cwd)
            return
