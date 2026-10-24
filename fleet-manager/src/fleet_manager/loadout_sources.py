"""Published JSON from an explicitly adopted Git source; never a client upload."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
from urllib.parse import urlsplit

from .planning import MAX_BYTES, PlanError, decode, identifier

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_REF = re.compile(r"refs/heads/[A-Za-z0-9][A-Za-z0-9_./-]{0,240}\Z")


@dataclass(frozen=True)
class PublishedDocument:
    source: str
    adoption_revision: str
    commit: str
    path: str
    encoded: bytes = field(repr=False)

    def provenance(self) -> dict:
        return {key: getattr(self, key) for key in (
            "source", "adoption_revision", "commit", "path",
        )}

    def document(self) -> object:
        return decode(self.encoded)


@dataclass(frozen=True)
class GitSource:
    """Trusted adoption input, not a request schema or credential provider.

    The repository owner supplies its existing ``run_caller_owned`` adapter:
    finite native process-tree custody, independent stdout/stderr bounds,
    headless spawning, explicit cwd/env, and no shell. The checkout and remote
    are already adopted by that owner; this class never clones or enrolls one.
    """

    source: str
    adoption_revision: str
    checkout: Path = field(repr=False)
    git: Path = field(repr=False)
    remote: str
    expected_remote: str = field(repr=False)
    ref: str
    path: str
    run: Callable[..., subprocess.CompletedProcess] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        for key in ("source", "adoption_revision", "remote"):
            identifier(getattr(self, key), f"source {key}")
        if (not isinstance(self.checkout, Path) or not self.checkout.is_absolute()
                or not isinstance(self.git, Path) or not self.git.is_absolute()
                or not self.checkout.is_dir() or not self.git.is_file()
                or not callable(self.run)):
            raise PlanError("source requires adopted absolute repository and executable paths")
        if (not isinstance(self.expected_remote, str) or not self.expected_remote
                or len(self.expected_remote.encode("utf-8")) > 4096
                or any(ord(c) < 32 for c in self.expected_remote)):
            raise PlanError("source requires an explicit non-secret adopted remote")
        if "://" in self.expected_remote:
            try:
                selected = urlsplit(self.expected_remote)
                if (selected.password is not None or selected.query or selected.fragment
                        or (selected.scheme in ("http", "https") and selected.username is not None)):
                    raise PlanError("source remote must not contain embedded credentials")
            except ValueError:
                raise PlanError("source remote identity is invalid") from None
        if (not isinstance(self.ref, str) or not _REF.fullmatch(self.ref)
                or ".." in self.ref or "//" in self.ref or "@{" in self.ref
                or self.ref.endswith(("/", ".", ".lock"))):
            raise PlanError("source requires a bounded published branch ref")
        if (not isinstance(self.path, str) or not self.path
                or len(self.path.encode("utf-8")) > 512 or "\\" in self.path
                or ":" in self.path or any(ord(c) < 32 for c in self.path)):
            raise PlanError("source requires a portable repository-relative JSON path")
        path = PurePosixPath(self.path)
        if (path.is_absolute() or any(part in ("", ".", "..") for part in self.path.split("/"))
                or path.suffix != ".json"):
            raise PlanError("source requires a portable repository-relative JSON path")

    def pull(self, *, deadline: float) -> PublishedDocument:
        """Fetch one adopted ref without checkout, hooks, submodules or file execution."""
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith("GIT_")}
        environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")

        def git(*arguments: str, limit: int = 4096) -> str:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlanError("published source acquisition deadline exceeded")
            try:
                result = self.run(
                    [str(self.git), "-c", "protocol.ext.allow=never",
                     "-c", f"core.hooksPath={os.devnull}", "-c", "gc.auto=0",
                     "-c", "maintenance.auto=false", *arguments],
                    cwd=str(self.checkout), env=environment, timeout=remaining,
                    max_stdout_bytes=limit, max_stderr_bytes=4096,
                )
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError, UnicodeError):
                raise PlanError("published source acquisition unavailable") from None
            if result.returncode != 0 or not isinstance(result.stdout, str):
                raise PlanError("published source acquisition refused")
            if len(result.stdout.encode("utf-8")) > limit:
                raise PlanError("published source output exceeds its bound")
            return result.stdout

        def remote() -> None:
            # Compare every configured fetch URL; no client-selected URL or token.
            if git("remote", "get-url", "--all", self.remote).splitlines() != [self.expected_remote]:
                raise PlanError("adopted source remote changed")

        remote()
        git("check-ref-format", self.ref)
        destination = f"refs/fleet-manager/{self.source}"
        git("fetch", "--quiet", "--no-tags", "--no-recurse-submodules",
            "--no-write-fetch-head", "--", self.remote, f"+{self.ref}:{destination}")
        remote()
        commit = git("rev-parse", "--verify", f"{destination}^{{commit}}").strip()
        if not _OID.fullmatch(commit):
            raise PlanError("published source commit identity is invalid")
        entry = git("ls-tree", "-z", commit, "--", self.path, limit=2048)
        parts = entry.rstrip("\0").split("\t")
        metadata = parts[0].split()
        if (len(parts) != 2 or parts[1] != self.path or len(metadata) != 3
                or metadata[:2] != ["100644", "blob"] or not _OID.fullmatch(metadata[2])):
            raise PlanError("published source must be an ordinary JSON blob")
        size = git("cat-file", "-s", metadata[2]).strip()
        if not size.isascii() or not size.isdecimal() or not 0 < int(size) <= MAX_BYTES:
            raise PlanError("published source exceeds its byte bound")
        encoded = git("cat-file", "blob", metadata[2], limit=MAX_BYTES).encode("utf-8")
        decode(encoded)
        return PublishedDocument(
            self.source, self.adoption_revision, commit, self.path, encoded,
        )
