"""Bind a complete registrar read to the Git tree whose ordering it claims."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import time
from typing import Literal

from .procutil import OutputLimitExceeded, run_background_capture
from .registrar_lane_aliases import _scrubbed_git_env

DocumentReads = dict[Path, str | None]
RevisionState = Literal["ordered", "unordered", "unavailable"]
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SUFFIXES = {".json", ".yaml", ".yml"}


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def record_document_read(reads: DocumentReads | None, path: Path, text: str) -> None:
    if reads is None:
        return
    path = path.resolve()
    digest = _text_digest(text)
    # A second parser read must not conceal an earlier, different document.
    reads[path] = digest if path not in reads or reads[path] == digest else None


@dataclass(frozen=True)
class SourceRevision:
    state: RevisionState = "unavailable"
    revision: str | None = None


def _git(root: Path, *args: str, timeout: float = 5.0):
    try:
        return run_background_capture(
            ["git", "--no-replace-objects", "--no-optional-locks", "--literal-pathspecs",
             "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
             "-C", str(root), *args],
            timeout=timeout, env=_scrubbed_git_env(), max_output=1024 * 1024,
        )
    except (OSError, UnicodeError, OutputLimitExceeded):
        return None


def _git_text(root: Path, *args: str) -> str | None:
    result = _git(root, *args)
    return result.stdout if result is not None and result.returncode == 0 else None


def _has_git_ancestor(root: Path) -> bool:
    for parent in (root, *root.parents):
        try:
            (parent / ".git").lstat()
        except FileNotFoundError:
            continue
        return True
    return False


def begin_source_revision(root: Path) -> SourceRevision:
    try:
        root = root.resolve(strict=True)
        if not root.is_dir():
            return SourceRevision()
        if not _has_git_ancestor(root):
            return SourceRevision("unordered")
        toplevel = _git_text(root, "rev-parse", "--show-toplevel")
        if not toplevel or Path(toplevel.strip()).resolve() != root:
            return SourceRevision()
        revision = _git_text(root, "rev-parse", "--verify", "HEAD")
        if revision is not None and _OBJECT_ID.fullmatch(revision.strip()):
            return SourceRevision("ordered", revision.strip())
    except (OSError, RuntimeError):
        pass
    return SourceRevision()


def finish_source_revision(
    root: Path, started: SourceRevision, *,
    directory_reads: Mapping[Path, str], document_reads: DocumentReads,
) -> SourceRevision:
    if (
        started.state == "unavailable" or not directory_reads
        or any(status != "complete" for status in directory_reads.values())
        or any(digest is None for digest in document_reads.values())
    ):
        return SourceRevision()
    try:
        root = root.resolve(strict=True)
        directories = {path.resolve().relative_to(root).as_posix() for path in directory_reads}
        documents = {
            path.resolve().relative_to(root).as_posix(): digest
            for path, digest in document_reads.items()
        }
        if any(str(PurePosixPath(path).parent) not in directories for path in documents):
            return SourceRevision()
        if begin_source_revision(root) != started:
            return SourceRevision()
        if started.state == "unordered":
            return started
        paths = sorted(directories)
        if _git_text(
            root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
            "--ignored=matching", "--", *paths,
        ) != "":
            return SourceRevision()
        tree = _git_text(root, "ls-tree", "-r", "-z", started.revision, "--", *paths)
        if tree is None:
            return SourceRevision()
        blobs = {}
        for entry in tree.split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            mode, kind, object_id = metadata.split()
            name = PurePosixPath(path)
            if str(name.parent) not in directories or name.suffix not in _SUFFIXES:
                continue
            if mode not in {"100644", "100755"} or kind != "blob" or not _OBJECT_ID.fullmatch(object_id):
                return SourceRevision()
            blobs[path] = object_id
        if blobs.keys() != documents.keys():
            return SourceRevision()
        for path, object_id in blobs.items():
            text = _git_text(root, "cat-file", "blob", object_id)
            if text is None or _text_digest(text) != documents[path]:
                return SourceRevision()
        # HEAD may have moved while reading the tree or comparing its blobs.
        return started if begin_source_revision(root) == started else SourceRevision()
    except (OSError, RuntimeError, ValueError):
        return SourceRevision()


def source_revision_follows(
    root: Path, previous: str | None, current: str, *, deadline: float | None = None,
) -> bool:
    """Only equal/ancestor revisions can advance a coordinator's source fence."""
    timeout = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
    if timeout <= 0:
        return False
    if not _OBJECT_ID.fullmatch(current) or (previous is not None and not _OBJECT_ID.fullmatch(previous)):
        return False
    if previous is None or previous == current:
        return True
    result = _git(root, "merge-base", "--is-ancestor", previous, current, timeout=timeout)
    return result is not None and result.returncode == 0
