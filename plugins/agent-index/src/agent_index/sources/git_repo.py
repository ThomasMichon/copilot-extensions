"""Local git repository source connector.

By default this connector indexes the **canonical default branch as fetched from
the repository's remote** (``origin/HEAD`` -> ``origin/main``|``origin/master``),
not the local working tree. That keeps the index reflecting what the team sees as
"the repo" -- the pushed/merged state -- rather than whatever branch happens to be
checked out, uncommitted edits, or a checkout that has drifted behind ``origin``.

Behaviour is environment-configurable:

- ``AGENT_INDEX_GIT_REMOTE`` (default ``origin``) -- the remote to track.
- ``AGENT_INDEX_GIT_REF`` (default ``<remote>/HEAD``) -- the ref to index. May be
  any revision (a remote-tracking branch, a tag, a SHA). ``<remote>/HEAD``
  resolves to the remote's default branch.
- ``AGENT_INDEX_GIT_FETCH`` (default on) -- fetch the remote before indexing so the
  tracked ref is fresh. Set to ``0``/``false``/``no`` to skip the network call and
  index whatever remote-tracking state already exists locally.

When there is no remote, the configured ref cannot be resolved, or the fetch and
resolution both fail (e.g. a purely local repo, or offline with no prior fetch),
the connector **falls back** to the local ``HEAD`` and the working tree -- the
original behaviour -- so local-first / standalone use keeps working. The engine
still contacts nothing but the local ``git`` executable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from agent_index.sources.base import FileEntry

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 1_000_000
_GIT = shutil.which("git") or "git"

_BINARY_EXTENSIONS = {
    ".7z",
    ".a",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".doc",
    ".docx",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lockb",
    ".mp3",
    ".mp4",
    ".o",
    ".obj",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".tgz",
    ".ttf",
    ".wasm",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".zip",
}

_LANGUAGES = {
    ".bash": "shell",
    ".bat": "batch",
    ".c": "c",
    ".cfg": "config",
    ".cmake": "cmake",
    ".conf": "config",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".ini": "config",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".lua": "lua",
    ".md": "markdown",
    ".ps1": "powershell",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_INDEXABLE_FILENAMES = {
    ".dockerignore": "config",
    ".editorconfig": "config",
    ".env.example": "config",
    ".gitattributes": "config",
    ".gitignore": "config",
    "dockerfile": "dockerfile",
    "license": "text",
    "makefile": "makefile",
    "readme": "markdown",
}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


class GitRepoConnector:
    """Source connector for a local git checkout.

    This connector uses the local ``git`` executable only. It never contacts a
    remote *service* API, so file and commit ingestion is naturally rate-friendly.
    By default it indexes the remote's default branch (``origin/HEAD``), fetching
    it first so the index tracks the canonical pushed state rather than the local
    working tree; see the module docstring for the configuration and fallback.
    """

    def __init__(
        self,
        source: str = "git",
        *,
        repo_path: str | os.PathLike[str] | None = None,
        remote: str | None = None,
        ref: str | None = None,
        fetch: bool | None = None,
    ):
        self._requested_source = source
        self.repo_path = Path(repo_path or os.environ.get("AGENT_INDEX_GIT_REPO") or os.getcwd())
        self.repo_path = self.repo_path.expanduser().resolve()
        self._name = self.repo_path.name
        self._file_source = f"git:{self._name}"
        self._commit_source = f"{self._file_source}:commits"

        self._remote = remote or os.environ.get("AGENT_INDEX_GIT_REMOTE") or "origin"
        self._ref = ref or os.environ.get("AGENT_INDEX_GIT_REF") or f"{self._remote}/HEAD"
        self._fetch = _env_flag("AGENT_INDEX_GIT_FETCH", True) if fetch is None else fetch

        # Resolved lazily on first discovery. ``_use_worktree`` True means we fell
        # back to the local HEAD + on-disk working tree; otherwise ``_index_ref``
        # / ``_index_commit`` name the fetched revision we read blobs from.
        self._resolved = False
        self._use_worktree = True
        self._index_ref: str | None = None
        self._index_commit: str | None = None

    @property
    def source_name(self) -> str:
        """Unique source name for the repository's tracked files."""
        return self._file_source

    # -- revision resolution -------------------------------------------------

    def _resolve(self) -> None:
        """Pick the revision to index: the fetched remote default branch, else
        fall back to the local working tree. Memoised per connector instance."""
        if self._resolved:
            return
        self._resolved = True
        try:
            if self._fetch and self._has_remote(self._remote):
                # Best effort: a failed fetch still lets us index prior
                # remote-tracking state (stale-but-canonical beats the worktree).
                self._git_quiet(["fetch", "--quiet", self._remote])
                # Ensure <remote>/HEAD points at the remote's default branch.
                self._git_quiet(["remote", "set-head", self._remote, "-a"])
            ref = self._resolve_ref()
            if ref is not None:
                commit = self._git(["rev-parse", ref]).strip()
                if commit:
                    self._index_ref = ref
                    self._index_commit = commit
                    self._use_worktree = False
                    return
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("git_repo: remote ref resolution failed (%s); indexing local HEAD", exc)
        # Fallback: local HEAD + working tree (purely local repo, or offline).
        self._use_worktree = True
        self._index_ref = None
        head = self._git_quiet(["rev-parse", "HEAD"]).strip()
        self._index_commit = head or None
        if self._fetch and self._has_remote(self._remote):
            logger.warning(
                "git_repo: could not resolve %s; falling back to local HEAD/working tree",
                self._ref,
            )

    def _resolve_ref(self) -> str | None:
        """Resolve the configured ref to a verifiable revision, or None."""
        head_sentinels = {f"{self._remote}/HEAD", "origin/HEAD", "HEAD"}
        if self._ref not in head_sentinels:
            candidates = [self._ref]
        else:
            candidates = [f"{self._remote}/HEAD", f"{self._remote}/main", f"{self._remote}/master"]
        for candidate in candidates:
            out = self._git_quiet(["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
            if out.strip():
                return candidate
        return None

    def _has_remote(self, remote: str) -> bool:
        out = self._git_quiet(["remote"])
        return remote in {line.strip() for line in out.splitlines() if line.strip()}

    def _index_rev(self) -> str:
        """The revision to read trees/blobs/log from (ref name or 'HEAD')."""
        return self._index_ref if (self._index_ref and not self._use_worktree) else "HEAD"

    # -- discovery -----------------------------------------------------------

    def discover(self, cancel_check: Callable[[], None] | None = None) -> list[FileEntry]:
        """Discover current tracked files plus commit-history entries."""
        self._resolve()
        entries = self._discover_files(self._tracked_paths(), cancel_check=cancel_check)
        entries.extend(self._discover_commits(None, cancel_check=cancel_check))
        return entries

    def discover_changed(
        self,
        last_commit: str | None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        """Discover files and commits changed after *last_commit*."""
        self._resolve()
        if last_commit is None:
            return self.discover(cancel_check=cancel_check)
        paths = self._changed_paths(last_commit)
        entries = self._discover_files(paths, cancel_check=cancel_check)
        entries.extend(self._discover_commits(last_commit, cancel_check=cancel_check))
        return entries

    def list_paths(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict[str, set[str]]:
        """Return current tracked file paths and commit entry paths."""
        self._resolve()
        file_paths: set[str] = set()
        for path in self._tracked_paths():
            if cancel_check:
                cancel_check()
            if self._is_indexable_path(path):
                file_paths.add(path)
        commit_paths = {f"commits/{sha}.txt" for sha in self._commit_shas()}
        return {self._file_source: file_paths, self._commit_source: commit_paths}

    def current_commit(self) -> str | None:
        """Return the SHA of the indexed revision (remote default branch, or the
        local HEAD when falling back)."""
        self._resolve()
        return self._index_commit

    def _tracked_paths(self) -> list[str]:
        if self._use_worktree:
            output = self._git(["ls-files", "-z"])
        else:
            output = self._git(["ls-tree", "-r", "--name-only", "-z", self._index_rev()])
        return [path for path in output.split("\0") if path]

    def _changed_paths(self, last_commit: str) -> list[str]:
        output = self._git(["diff", "--name-status", f"{last_commit}..{self._index_rev()}", "--"])
        paths: list[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("D"):
                continue
            if status.startswith(("R", "C")) and len(parts) >= 3:
                paths.append(parts[2])
            elif len(parts) >= 2:
                paths.append(parts[1])
        return paths

    def _discover_files(
        self,
        paths: list[str],
        *,
        cancel_check: Callable[[], None] | None,
    ) -> list[FileEntry]:
        commit = self._index_commit
        entries: list[FileEntry] = []
        for path in paths:
            if cancel_check:
                cancel_check()
            entry = self._read_file_entry(path, commit)
            if entry is not None:
                entries.append(entry)
        return entries

    def _read_file_entry(self, path: str, head: str | None) -> FileEntry | None:
        if not self._is_indexable_path(path):
            return None
        raw = self._read_blob(path)
        if raw is None or len(raw) > _MAX_FILE_BYTES:
            return None
        if b"\0" in raw:
            return None
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        language = self._detect_language(path)
        return FileEntry(
            path=path,
            content=content,
            language=language,
            source=self._file_source,
            metadata={
                "type": "file",
                "repo": self._name,
                "commit": head,
                "size_bytes": len(raw),
            },
        )

    def _read_blob(self, path: str) -> bytes | None:
        """Read a file's bytes from the indexed revision, or the working tree
        when falling back."""
        if self._use_worktree:
            full_path = self.repo_path / Path(path)
            try:
                if not full_path.is_file():
                    return None
                return full_path.read_bytes()
            except OSError:
                return None
        try:
            return self._git_bytes(["show", f"{self._index_rev()}:{path}"])
        except (subprocess.CalledProcessError, OSError):
            return None

    def _discover_commits(
        self,
        last_commit: str | None,
        *,
        cancel_check: Callable[[], None] | None,
    ) -> list[FileEntry]:
        args = [
            "log",
            "--date=iso-strict",
            "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e",
        ]
        if last_commit:
            args.append(f"{last_commit}..{self._index_rev()}")
        else:
            args.append(self._index_rev())
        output = self._git(args)
        entries: list[FileEntry] = []
        for record in output.split("\x1e"):
            if cancel_check:
                cancel_check()
            record = record.strip("\n")
            if not record:
                continue
            fields = record.split("\x1f", 5)
            if len(fields) < 6:
                continue
            sha, author_name, author_email, authored_at, subject, body = fields
            content = (
                f"Commit: {sha}\n"
                f"Author: {author_name} <{author_email}>\n"
                f"Date: {authored_at}\n\n"
                f"{subject.strip()}\n"
            )
            if body.strip():
                content += f"\n{body.strip()}\n"
            entries.append(
                FileEntry(
                    path=f"commits/{sha}.txt",
                    content=content,
                    language="commit",
                    source=self._commit_source,
                    metadata={
                        "type": "commit",
                        "repo": self._name,
                        "sha": sha,
                        "author_name": author_name,
                        "author_email": author_email,
                        "authored_at": authored_at,
                        "subject": subject.strip(),
                    },
                )
            )
        return entries

    def _commit_shas(self) -> list[str]:
        output = self._git(["log", "--format=%H", self._index_rev()])
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _git(self, args: list[str]) -> str:
        completed = subprocess.run(  # noqa: S603
            [_GIT, *args],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout

    def _git_quiet(self, args: list[str]) -> str:
        """Run git without raising on failure; return stdout ('' on error)."""
        try:
            completed = subprocess.run(  # noqa: S603
                [_GIT, *args],
                cwd=self.repo_path,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return ""
        return completed.stdout if completed.returncode == 0 else ""

    def _git_bytes(self, args: list[str]) -> bytes:
        completed = subprocess.run(  # noqa: S603
            [_GIT, *args],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
        )
        return completed.stdout

    @classmethod
    def _is_indexable_path(cls, path: str) -> bool:
        normalized = path.replace("\\", "/")
        parts = set(normalized.split("/"))
        if parts & {
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "__pycache__",
            "node_modules",
            "vendor",
            "dist",
            "build",
        }:
            return False
        suffix = Path(normalized).suffix.lower()
        if suffix in _BINARY_EXTENSIONS:
            return False
        return cls._detect_language(normalized) != "unknown"

    @staticmethod
    def _detect_language(path: str) -> str:
        name = Path(path).name.lower()
        if name in _INDEXABLE_FILENAMES:
            return _INDEXABLE_FILENAMES[name]
        suffix = Path(path).suffix.lower()
        return _LANGUAGES.get(suffix, "unknown")
