"""Private push transport, shared by ancestry and proved owned-PR replay policies."""

from __future__ import annotations

import subprocess
import logging
import tempfile
import shlex
import uuid
import os
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from collections.abc import Iterator, Sequence

from . import git_ops, git_push_binding, git_push_tracking, pr_transport_fence, push_timeout
from .github_credentials import GitAuthArgs

_destination: ContextVar[
    tuple[str, tuple[str, ...], tuple[tuple[str, str], ...], str | None] | None
] = ContextVar("push_destination", default=None)
REMOTE_TRANSPORT_SETTINGS = ("receivepack", "uploadpack", "proxy", "proxyauthmethod", "vcs", "mirror", "pushoption")


def _auth_attempts(auth: list[str]) -> list[list[str]]:
    if auth and isinstance(auth, GitAuthArgs) and auth.allow_ambient_retry:
        return [auth, []]
    return [auth]


@contextmanager
def pinned_destination(remote: str, urls: tuple[str, ...],
                       settings: tuple[tuple[str, str], ...] = (), *, fetch_url: str | None = None) -> Iterator[None]:
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
def _url_aliases(
    pairs: Sequence[tuple[str, str]], *, logical_remote: str = "", logical_fetch: str = "",
) -> Iterator[list[str]]:
    def quoted(value: str) -> str:
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
def _hook_identity(remote: str, cwd: str | Path, *, source: str, published_oid: str, target: str) -> Iterator[list[str]]:
    """Retain the real hook's logical remote and original source-ref identity."""
    hook = git_ops.git("rev-parse", "--git-path", "hooks/pre-push", cwd=cwd,
                       isolated_repository=True).stdout.strip()
    path = Path(cwd, hook).resolve()
    if not path.is_file() or (os.name != "nt" and not os.access(path, os.X_OK)):
        yield []
        return
    hook_source = source.removeprefix("+")
    binding = git_push_binding.current.get()
    if binding is not None and hook_source == binding.object_id:
        hook_source = binding.hook_ref
    elif hook_source and hook_source != "HEAD":
        hook_source = git_ops.git(
            "rev-parse", "--symbolic-full-name", hook_source, cwd=cwd,
            isolated_repository=True,
        ).stdout.strip() or hook_source
    stdin = ""
    if published_oid:
        stdin = (
            "while read -r local_ref local_sha remote_ref remote_sha; do\n"
            f"  if [ \"$local_ref\" = {shlex.quote(published_oid)} ] && "
            f"[ \"$local_sha\" = {shlex.quote(published_oid)} ] && "
            f"[ \"$remote_ref\" = {shlex.quote(target)} ]; then\n"
            f"    local_ref={shlex.quote(hook_source)}\n  fi\n"
            '  printf "%s %s %s %s\\n" "$local_ref" "$local_sha" "$remote_ref" "$remote_sha"\n'
            "done | "
        )
    with tempfile.TemporaryDirectory(prefix="agent-worktrees-push-hook-") as directory:
        proxy = Path(directory) / "pre-push"
        proxy.write_text(
            "#!/bin/sh\n" + stdin + shlex.quote(path.as_posix()) + " " +
            shlex.quote(remote) + ' "$2"\n', encoding="utf-8", newline="\n",
        )
        proxy.chmod(0o700)
        yield ["-c", f"core.hooksPath={Path(directory).as_posix()}"]


@git_push_binding.capture
@pr_transport_fence.publication
def push(
    remote: str, branch: str, *, cwd: str | Path,
    force_with_lease: bool = False, force_with_lease_expect: str | None = None,
    timeout: float | None = push_timeout.DEFAULT_PUSH_TIMEOUT,
) -> git_ops.PushResult:
    pinned = _destination.get()
    destinations = pinned[1] if pinned and pinned[0] == remote else ()
    binding = git_push_binding.current.get()
    published_oid = binding.object_id if binding is not None else ""
    source, separator, target = branch.partition(":")
    if destinations:
        if not separator:
            return git_ops.PushResult(ok=False, stderr="Pinned transport requires its resolved destination ref.")
        if source:
            try:
                binding = git_push_binding.current.get()
                published_oid = binding.object_id if binding is not None else git_ops.git(
                    "rev-parse", "--verify", source.removeprefix("+") + "^{object}",
                    cwd=cwd, isolated_repository=True, timeout=timeout, kill_tree=True,
                ).stdout.strip()
            except (git_ops.GitError, subprocess.SubprocessError) as exc:
                return git_ops.PushResult(ok=False, stderr=f"Cannot pin the pushed source object: {exc}")
            if not published_oid:
                return git_ops.PushResult(ok=False, stderr="The pushed source object is unavailable.")
            branch = ("+" if source.startswith("+") else "") + published_oid + ":" + target
    extra = ([f"--force-with-lease={branch.rsplit(':', 1)[-1]}:{force_with_lease_expect}"]
             if force_with_lease_expect is not None else
             ["--force-with-lease"] if force_with_lease else [])
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
    try:
        auth_args = (git_ops._auth_config_args_for_url(auth_destination) if pinned and pinned[0] == remote
                     else git_ops._auth_config_args(remote, cwd=cwd))
    except git_ops.GitError as exc:
        return git_ops.PushResult(ok=False, stderr=exc.stderr)
    attempts = _auth_attempts(auth_args)
    deletion = None
    if not source and pinned and pinned[0] == remote:
        try:
            deletion = git_push_tracking.capture_deletion(
                remote, destinations, target, cwd, fetch_url=pinned[3],
            )
        except (OSError, git_ops.GitError, TimeoutError, subprocess.SubprocessError) as exc:
            return git_ops.PushResult(ok=False, stderr=f"Cannot capture pre-transport deletion tracking: {exc}")
    last_stderr = last_stdout = ""
    for prefix in attempts:
        try:
            aliases = [(url, f"{transport_remote}-url-{index}") for index, url in enumerate(destinations)]
            fetch_url = (pinned[3] or "") if pinned is not None else ""
            alias_context: AbstractContextManager[list[str]] = nullcontext([])
            hook_context: AbstractContextManager[list[str]] = nullcontext([])
            if destinations:
                alias_context = _url_aliases(aliases, logical_remote=remote, logical_fetch=fetch_url)
            if destinations or binding is not None:
                hook_context = _hook_identity(remote, cwd, source=source, published_oid=published_oid, target=target)
            with alias_context as alias_args, hook_context as hook_args:
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
                    _refresh_tracking(
                        remote, destinations, branch, cwd, force_with_lease_expect,
                        published_oid=published_oid,
                        **({"deletion": deletion} if not source else {}),
                    )
                except (OSError, git_ops.GitError, TimeoutError, subprocess.TimeoutExpired) as exc:
                    if isinstance(exc, subprocess.TimeoutExpired):
                        detail = f"tracking refresh timed out after {exc.timeout:g} seconds"
                    else:
                        detail = exc.stderr if isinstance(exc, git_ops.GitError) else str(exc)
                    warning = (f"Push succeeded, but local remote-tracking refresh failed: "
                               f"{type(exc).__name__}: {detail.strip() or 'no further diagnostic details'}.")
                    logging.getLogger("agent-worktrees").warning(warning)
            return git_ops.PushResult(ok=True, stderr=warning, published_head_sha=published_oid)
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
        for prefix in _auth_attempts(auth):
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
                      expected: str | None = None, *, published_oid: str,
                      deletion: git_push_tracking.Deletion | None = None) -> None:
    """Preserve Git's named-remote tracking update only for the still-matching remote."""
    pinned = _destination.get()
    git_push_tracking.refresh(
        remote, destinations, refspec, cwd, expected, published_oid=published_oid,
        fetch_url=pinned[3] if pinned and pinned[0] == remote else None, deletion=deletion,
    )
