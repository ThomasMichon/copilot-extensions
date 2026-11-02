"""Explicit immutable local Git input; no image build, guest or service action."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from process import run as _run

MAX_FILES = 16_000
MAX_SOURCE_BYTES = 256 * 1024**2
MAX_BLOB_BYTES = 8 * 1024**2
MAX_LISTING_BYTES = 4 * 1024**2
MAX_PREFIXES = 32_000
MAX_PREFIX_BYTES = 8 * 1024**2
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class SourceError(RuntimeError):
    """Source preparation refused; retain any incomplete artifact without retry."""


@dataclass(frozen=True)
class SourceTree:
    commit: str
    tree: str
    files: int
    logical_bytes: int


def _path_key(name: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def _check_path_aliases(
    name: str, prefixes: dict[str, tuple[str, bool]], prefix_bytes: int,
) -> int:
    parts = name.split("/")
    for length in range(1, len(parts) + 1):
        original = "/".join(parts[:length])
        key = _path_key(original)
        entry = (original, length == len(parts))
        if (
            _path_key(parts[length - 1]) == ".git"
            or key in prefixes and (prefixes[key] != entry or entry[1])
        ):
            raise SourceError("source tree contains filesystem path aliases")
        if key not in prefixes:
            prefix_bytes += len(original.encode("utf-8")) + len(key.encode("utf-8"))
            if len(prefixes) >= MAX_PREFIXES or prefix_bytes > MAX_PREFIX_BYTES:
                raise SourceError("source path-prefix inventory exceeds its supported ceiling")
            prefixes[key] = entry
    return prefix_bytes


def inspect_tree(commit: str, tree: str, listing: str) -> SourceTree:
    """Bound regular source inputs before materializing their shallow closure."""
    if (
        _OID.fullmatch(commit) is None or _OID.fullmatch(tree) is None
        or len(tree) != len(commit)
    ):
        raise SourceError("source commit or tree identity is incomplete")
    seen: set[str] = set()
    prefixes: dict[str, tuple[str, bool]] = {}
    prefix_bytes = 0
    total = 0
    if len(listing) > MAX_LISTING_BYTES or len(listing.encode("utf-8")) > MAX_LISTING_BYTES:
        raise SourceError("source tree listing exceeds its supported byte ceiling")
    if not listing.endswith("\0"):
        raise SourceError("source tree listing is incomplete")
    for record in listing[:-1].split("\0"):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        path = PurePosixPath(name)
        if (
            not separator or len(fields) != 4 or fields[0] not in {"100644", "100755"}
            or fields[1] != "blob" or _OID.fullmatch(fields[2]) is None
            or len(fields[2]) != len(commit)
            or not name or path.is_absolute() or ".." in path.parts or "." in name.split("/")
            or ".git" in path.parts or str(path) != name or name in seen
            or len(name.encode("utf-8")) > 1024
        ):
            raise SourceError("source tree contains unsupported paths or nonregular objects")
        try:
            size = int(fields[3])
        except ValueError as error:
            raise SourceError("source blob size is unavailable") from error
        if size < 0 or size > MAX_BLOB_BYTES:
            raise SourceError("source blob exceeds its supported byte ceiling")
        prefix_bytes = _check_path_aliases(name, prefixes, prefix_bytes)
        seen.add(name)
        total += size
        if len(seen) > MAX_FILES or total > MAX_SOURCE_BYTES:
            raise SourceError("source tree exceeds its supported input ceiling")
    return SourceTree(commit, tree, len(seen), total)


def _private_results(root: Path, repository: Path) -> None:
    if (
        not root.is_absolute() or root.resolve(strict=True) != root
        or root in {Path("/"), Path.home(), repository}
        or repository in root.parents or "session-state" in root.parts
    ):
        raise SourceError("select an existing private results root outside repositories and session state")
    info = root.stat()
    if (
        os.name != "posix" or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid() or info.st_mode & 0o077
    ):
        raise SourceError("source results root must be a private caller-owned directory")
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise SourceError("source results must not be nested in a repository")


class SourceReader:
    """Prepare a fresh, credential-free shallow checkout from an explicit local commit."""

    def __init__(
        self, git: Path, *, run: Callable[..., subprocess.CompletedProcess[str]] = _run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not git.is_absolute():
            raise ValueError("Git executable must be selected by absolute path")
        self.git, self.run, self.clock = git, run, clock

    def materialize(self, repository: Path, commit: str, results: Path, name: str) -> SourceTree:
        if (
            not repository.is_absolute() or repository.resolve(strict=True) != repository
            or _OID.fullmatch(commit) is None
            or re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name) is None or len(name) > 64
        ):
            raise SourceError("select a normalized local repository, full commit and fresh artifact name")
        _private_results(results, repository)
        target = results / name
        target.mkdir(mode=0o700)
        template, checkout = target / "empty-template", target / "source"
        template.mkdir(mode=0o700)
        env = {
            "PATH": os.defpath, "HOME": str(target), "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
            "GIT_AUTHOR_NAME": "Synthetic Lab", "GIT_AUTHOR_EMAIL": "lab@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Lab", "GIT_COMMITTER_EMAIL": "lab@example.invalid",
        }
        deadline = self.clock() + 60
        prefix = [
            str(self.git), "-c", f"core.hooksPath={template}", "-c", "core.fsmonitor=false",
            "-c", f"core.attributesFile={os.devnull}",
            "-c", "core.autocrlf=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
        ]

        def invoke(root: Path, *arguments: str) -> str:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise SourceError("source preparation deadline exhausted")
            try:
                result = self.run(
                    [*prefix, "-C", str(root), *arguments], timeout=min(15, remaining),
                    env=env, max_stdout_bytes=MAX_LISTING_BYTES,
                )
            except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError) as error:
                raise SourceError(f"source command unavailable ({type(error).__name__})") from error
            if result.returncode != 0:
                raise SourceError(f"source command exited with status {result.returncode}")
            if self.clock() >= deadline:
                raise SourceError("source preparation deadline exhausted")
            return result.stdout

        resolved = invoke(repository, "rev-parse", "--verify", "--end-of-options", commit + "^{commit}").strip()
        if resolved != commit:
            raise SourceError("selected source commit differs")
        tree = invoke(repository, "rev-parse", "--verify", "--end-of-options", commit + "^{tree}").strip()
        listing = invoke(repository, "ls-tree", "-rlz", "--full-tree", commit)
        observed = inspect_tree(commit, tree, listing)
        object_format = "sha256" if len(commit) == 64 else "sha1"
        invoke(
            target, "init", "--quiet", f"--object-format={object_format}",
            f"--template={template}", str(checkout),
        )
        invoke(
            checkout, "fetch", "--quiet", "--depth=1", "--no-tags", "--no-write-fetch-head",
            "--no-recurse-submodules", "--no-auto-maintenance", "--", str(repository), commit,
        )
        invoke(checkout, "checkout", "--quiet", "--detach", "--no-recurse-submodules", commit)
        if invoke(checkout, "rev-parse", "HEAD").strip() != commit:
            raise SourceError("materialized source commit differs")
        if invoke(checkout, "rev-parse", "HEAD^{tree}").strip() != tree:
            raise SourceError("materialized source tree differs")
        with (target / "source.json").open("x", encoding="utf-8") as output:
            json.dump(asdict(observed), output, sort_keys=True)
            output.write("\n")
        if self.clock() >= deadline:
            raise SourceError("source preparation deadline exhausted")
        return observed
