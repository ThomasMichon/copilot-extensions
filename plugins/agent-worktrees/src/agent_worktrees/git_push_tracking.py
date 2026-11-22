"""Refresh pinned remote-tracking refs without overwriting concurrent observations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import git_ops, pr_publish


@dataclass(frozen=True)
class Deletion:
    tracked: str
    previous: str
    mappings: tuple[str, ...]


def _mapping(remote: str, target: str, cwd: str | Path) -> tuple[str, tuple[str, ...]]:
    mappings = tuple(git_ops.git(
        "config", "--get-all", f"remote.{remote}.fetch", cwd=cwd,
        check=False, isolated_repository=True,
    ).stdout.splitlines())
    for mapping in mappings:
        if not mapping.startswith("^"):
            continue
        excluded = mapping[1:]
        if excluded == target:
            return "", mappings
        if excluded.count("*") == 1:
            prefix, suffix = excluded.split("*")
            if target.startswith(prefix) and target.endswith(suffix) and len(target) >= len(prefix) + len(suffix):
                return "", mappings
    for mapping in mappings:
        source, _, tracked = mapping.lstrip("+").partition(":")
        if source == target and tracked:
            return tracked, mappings
        if source.count("*") == 1 and tracked.count("*") == 1:
            prefix, suffix = source.split("*")
            if not target.startswith(prefix) or (suffix and not target.endswith(suffix)):
                continue
            end = len(target) - len(suffix) if suffix else len(target)
            if end >= len(prefix):
                return tracked.replace("*", target[len(prefix):end]), mappings
    return "", mappings


def _same_remote(remote: str, destinations: tuple[str, ...], fetch_url: str | None,
                 cwd: str | Path) -> bool:
    current = git_ops.git("remote", "get-url", "--push", "--all", remote,
                          cwd=cwd, check=False, isolated_repository=True)
    if current.returncode or tuple(current.stdout.splitlines()) != destinations:
        return False
    if fetch_url is not None:
        current = git_ops.git("remote", "get-url", remote, cwd=cwd,
                              check=False, isolated_repository=True)
        if current.returncode or current.stdout.rstrip("\r\n") != fetch_url:
            return False
    return True


def capture_deletion(remote: str, destinations: tuple[str, ...], target: str,
                     cwd: str | Path, *, fetch_url: str | None) -> Deletion | None:
    """Capture the only local tracking observation this deletion may remove."""
    with pr_publish.publish_lock(str(cwd), acquire_timeout=1.0):
        if not _same_remote(remote, destinations, fetch_url, cwd):
            return None
        tracked, mappings = _mapping(remote, target, cwd)
        previous = git_ops.git(
            "rev-parse", "--verify", tracked, cwd=cwd, check=False, isolated_repository=True,
        ).stdout.strip() if tracked else ""
        return Deletion(tracked, previous, mappings)


def refresh(remote: str, destinations: tuple[str, ...], refspec: str, cwd: str | Path,
            expected: str | None, *, published_oid: str, fetch_url: str | None,
            deletion: Deletion | None = None) -> None:
    source, separator, target = refspec.partition(":")
    if not separator:
        target = "refs/heads/" + refspec.removeprefix("refs/heads/")
    if not target.startswith("refs/heads/") or (not published_oid and source):
        return
    with pr_publish.publish_lock(str(cwd), acquire_timeout=1.0):
        if not _same_remote(remote, destinations, fetch_url, cwd):
            return
        tracked, mappings = _mapping(remote, target, cwd)
        if not tracked:
            return
        if not source:
            if deletion is not None and (tracked, mappings) == (deletion.tracked, deletion.mappings) and deletion.previous:
                git_ops.git("update-ref", "-d", tracked, deletion.previous, cwd=cwd, isolated_repository=True)
            return
        previous = git_ops.git("rev-parse", "--verify", tracked, cwd=cwd,
                               check=False, isolated_repository=True).stdout.strip()
        if previous and previous != expected and not git_ops.is_commit_ancestor(previous, published_oid, cwd=cwd):
            return
        git_ops.git("update-ref", tracked, published_oid, previous or "0" * len(published_oid), cwd=cwd,
                    isolated_repository=True)
