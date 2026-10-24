"""Private push transport, shared by ancestry and proved owned-PR replay policies."""

from __future__ import annotations

import subprocess
import logging
import tempfile
import shlex
import uuid
import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from . import git_ops, push_timeout

_destination: ContextVar[
    tuple[str, tuple[str, ...], tuple[tuple[str, str], ...], str | None] | None
] = ContextVar("push_destination", default=None)
REMOTE_TRANSPORT_SETTINGS = ("receivepack", "uploadpack", "proxy", "proxyauthmethod", "vcs", "mirror", "pushoption")


@contextmanager
def pinned_destination(remote: str, urls: tuple[str, ...],
                       settings: tuple[tuple[str, str], ...] = (), *, fetch_url: str | None = None):
    """Bind this publication's transport/auth to an already verified URL."""
    token = _destination.set((remote, urls, settings, fetch_url))
    try:
        yield
    finally:
        _destination.reset(token)

def captured_urls(remote: str) -> tuple[str, ...]:
    snapshot = _destination.get()
    return snapshot[1] if snapshot and snapshot[0] == remote else ()


@contextmanager
def _url_aliases(pairs, *, logical_remote: str = "", logical_fetch: str = ""):
    def quoted(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'
    with tempfile.TemporaryDirectory(prefix="agent-worktrees-url-config-") as directory:
        config = Path(directory) / "pin.config"
        content = "".join(
            f"[url {quoted(url)}]\n\tinsteadOf = {quoted(alias)}\n"
            for url, alias in pairs
        )
        if logical_remote:
            fetch_alias = f"agent-worktrees-hook-fetch-{uuid.uuid4().hex}"
            fetch_url = logical_fetch or pairs[0][0]
            content += f"[url {quoted(fetch_url)}]\n\tinsteadOf = {quoted(fetch_alias)}\n"
            content += (f"[remote {quoted(logical_remote)}]\n\turl = \"\"\n"
                        f"\turl = {quoted(fetch_alias)}\n\tpushurl = \"\"\n")
            content += "".join(f"\tpushurl = {quoted(alias)}\n" for _url, alias in pairs)
        config.write_text(content, encoding="utf8")
        yield ["-c", f"include.path={config.as_posix()}"]


@contextmanager
def _hook_identity(remote: str, cwd: str | Path):
    """Forward the real pre-push hook unchanged except for its logical remote name."""
    hook = git_ops.git("rev-parse", "--git-path", "hooks/pre-push", cwd=cwd,
                       isolated_repository=True).stdout.strip()
    path = Path(cwd, hook).resolve()
    if not path.is_file() or (os.name != "nt" and not os.access(path, os.X_OK)):
        yield []
        return
    with tempfile.TemporaryDirectory(prefix="agent-worktrees-push-hook-") as directory:
        proxy = Path(directory) / "pre-push"
        proxy.write_text(
            "#!/bin/sh\nexec " + shlex.quote(path.as_posix()) + " " +
            shlex.quote(remote) + ' "$2"\n', encoding="utf-8", newline="\n",
        )
        proxy.chmod(0o700)
        yield ["-c", f"core.hooksPath={Path(directory).as_posix()}"]


def push(
    remote: str, branch: str, *, cwd: str | Path,
    force_with_lease: bool = False, force_with_lease_expect: str | None = None,
    timeout: float | None = push_timeout.DEFAULT_PUSH_TIMEOUT,
) -> git_ops.PushResult:
    extra = ([f"--force-with-lease={branch.rsplit(':', 1)[-1]}:{force_with_lease_expect}"]
             if force_with_lease_expect is not None else
             ["--force-with-lease"] if force_with_lease else [])
    pinned = _destination.get()
    destinations = pinned[1] if pinned and pinned[0] == remote else ()
    destination = destinations[0] if destinations else remote
    transport_remote = f"agent-worktrees-pinned-{uuid.uuid4().hex}" if destinations else remote
    destination_args = ([
        "-c", f"remote.{transport_remote}.url={destination}",
        *[argument for key, value in pinned[2] for argument in (
            "-c", f"remote.{transport_remote}.{key}={value}",
        )],
        *[argument for index, url in enumerate(destinations) for argument in (
            "-c", f"remote.{transport_remote}.pushurl={transport_remote}-url-{index}",
        )],
    ] if pinned and pinned[0] == remote else [])
    auth_destination = next((url for url in destinations if url.lower().startswith("https://")), destination)
    auth_args = (git_ops._auth_config_args_for_url(auth_destination) if pinned and pinned[0] == remote
                 else git_ops._auth_config_args(remote, cwd=cwd))
    attempts = [auth_args, []] if auth_args else [[]]
    last_stderr = last_stdout = ""
    for prefix in attempts:
        try:
            aliases = [(url, f"{transport_remote}-url-{index}") for index, url in enumerate(destinations)]
            with _url_aliases(aliases, logical_remote=remote, logical_fetch=pinned[3] or "") if destinations else nullcontext([]) as alias_args, \
                    _hook_identity(remote, cwd) if destinations else nullcontext([]) as hook_args:
                result = git_ops.git(
                    *prefix, *destination_args, *alias_args, *hook_args, "push", transport_remote, branch, *extra, "--quiet",
                    cwd=cwd, check=False, timeout=timeout, kill_tree=True,
                    isolated_repository=True,
                )
        except subprocess.TimeoutExpired as exc:
            return git_ops.PushResult(ok=False, stderr=push_timeout.message(exc, timeout))
        if result.returncode == 0:
            warning = ""
            if pinned and pinned[0] == remote:
                try:
                    _refresh_tracking(remote, destinations, branch, cwd, timeout,
                                      force_with_lease_expect)
                except (OSError, git_ops.GitError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    if isinstance(exc, subprocess.TimeoutExpired):
                        detail = f"remote-ref probe timed out after {exc.timeout:g} seconds"
                    else:
                        detail = exc.stderr if isinstance(exc, git_ops.GitError) else str(exc)
                    warning = (f"Push succeeded, but local remote-tracking refresh failed: "
                               f"{type(exc).__name__}: {detail.strip() or 'no further diagnostic details'}.")
                    logging.getLogger("agent-worktrees").warning(warning)
            return git_ops.PushResult(ok=True, stderr=warning)
        last_stderr, last_stdout = result.stderr or last_stderr, result.stdout or last_stdout
    return git_ops.PushResult(ok=False, stderr=last_stderr, stdout=last_stdout)


def observed_ref(remote: str, destinations: tuple[str, ...], target: str,
                 cwd: str | Path) -> str:
    """Read a pinned exact ref: its SHA, empty when absent, or raise on failure."""
    alias = f"agent-worktrees-observe-{uuid.uuid4().hex}-url"
    pinned = _destination.get()
    probe_settings = tuple((key, value) for key, value in (pinned[2] if pinned else ())
                           if key in ("uploadpack", "proxy", "proxyauthmethod", "vcs"))
    probe_remote = f"agent-worktrees-probe-{uuid.uuid4().hex}"
    auth = git_ops._auth_config_args_for_url(destinations[0])
    with _url_aliases([(destinations[0], alias)]) as alias_args:
        for prefix in [auth, []] if auth else [[]]:
            published = git_ops.git(
                *prefix, *alias_args,
                "-c", f"remote.{probe_remote}.url={alias}",
                *[argument for key, value in probe_settings for argument in (
                    "-c", f"remote.{probe_remote}.{key}={value}",
                )],
                "ls-remote", probe_remote, target, cwd=cwd,
                check=False, timeout=30, kill_tree=True, isolated_repository=True,
            )
            if published.returncode == 0:
                break
    rows = [line.split() for line in published.stdout.splitlines()]
    sha = next((row[0] for row in rows if len(row) == 2 and row[1] == target), "")
    if published.returncode:
        raise OSError("Cannot observe published remote ref for tracking refresh")
    return sha


def published_ref(remote: str, destinations: tuple[str, ...], target: str,
                  cwd: str | Path) -> str:
    """Require a present pinned ref for post-push tracking refresh."""
    sha = observed_ref(remote, destinations, target, cwd)
    if not sha:
        raise OSError("Cannot observe published remote ref for tracking refresh")
    return sha


def _refresh_tracking(remote: str, destinations: tuple[str, ...], refspec: str, cwd: str | Path,
                      timeout: float | None, expected: str | None = None) -> None:
    """Preserve Git's named-remote tracking update only for the still-matching remote."""
    from . import pr_publish

    _, separator, target = refspec.partition(":")
    if not separator:
        target = "refs/heads/" + refspec.removeprefix("refs/heads/")
    if not target.startswith("refs/heads/"):
        return
    sha = published_ref(remote, destinations, target, cwd)
    with pr_publish.publish_lock(str(cwd), acquire_timeout=1.0):
        current = git_ops.git("remote", "get-url", "--push", "--all", remote,
                              cwd=cwd, check=False, isolated_repository=True)
        if current.returncode or tuple(current.stdout.splitlines()) != destinations:
            return
        pinned = _destination.get()
        if pinned and pinned[0] == remote and pinned[3] is not None:
            current_fetch = git_ops.git("remote", "get-url", remote, cwd=cwd,
                                        check=False, isolated_repository=True)
            if current_fetch.returncode or current_fetch.stdout.rstrip("\r\n") != pinned[3]:
                return
        mappings = git_ops.git("config", "--get-all", f"remote.{remote}.fetch",
                               cwd=cwd, check=False, isolated_repository=True).stdout.splitlines()
        for mapping in mappings:
            if not mapping.startswith("^"):
                continue
            excluded = mapping[1:]
            if excluded == target:
                return
            if excluded.count("*") == 1:
                prefix, suffix = excluded.split("*")
                if target.startswith(prefix) and target.endswith(suffix) and len(target) >= len(prefix) + len(suffix):
                    return
        for mapping in mappings:
            fetch_source, _, fetch_target = mapping.lstrip("+").partition(":")
            if fetch_source == target and fetch_target:
                tracked = fetch_target
            elif fetch_source.count("*") == 1 and fetch_target.count("*") == 1:
                prefix, suffix = fetch_source.split("*")
                if not target.startswith(prefix) or (suffix and not target.endswith(suffix)):
                    continue
                end = len(target) - len(suffix) if suffix else len(target)
                if end < len(prefix):
                    continue
                tracked = fetch_target.replace("*", target[len(prefix):end])
            else:
                continue
            previous = git_ops.git("rev-parse", "--verify", tracked, cwd=cwd,
                                   check=False, isolated_repository=True).stdout.strip()
            if previous and previous != expected and not git_ops.is_commit_ancestor(previous, sha, cwd=cwd):
                # Another completed publication may already have advanced this
                # tracking ref. Never roll it back to an older successful push.
                return
            git_ops.git("update-ref", tracked, sha, previous or "0" * len(sha), cwd=cwd,
                        isolated_repository=True)
            return
