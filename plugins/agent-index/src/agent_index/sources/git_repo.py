"""Local git repository source connector."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from agent_index.sources.base import FileEntry

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


class GitRepoConnector:
    """Source connector for a local git checkout.

    This connector uses the local ``git`` executable only. It never contacts a
    remote service, so file and commit ingestion is naturally rate-friendly.
    """

    def __init__(self, source: str = "git", *, repo_path: str | os.PathLike[str] | None = None):
        self._requested_source = source
        self.repo_path = Path(repo_path or os.environ.get("AGENT_INDEX_GIT_REPO") or os.getcwd())
        self.repo_path = self.repo_path.expanduser().resolve()
        self._name = self.repo_path.name
        self._file_source = f"git:{self._name}"
        self._commit_source = f"{self._file_source}:commits"

    @property
    def source_name(self) -> str:
        """Unique source name for the repository's tracked files."""
        return self._file_source

    def discover(self, cancel_check: Callable[[], None] | None = None) -> list[FileEntry]:
        """Discover current tracked files plus commit-history entries."""
        entries = self._discover_files(self._tracked_paths(), cancel_check=cancel_check)
        entries.extend(self._discover_commits(None, cancel_check=cancel_check))
        return entries

    def discover_changed(
        self,
        last_commit: str | None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        """Discover files and commits changed after *last_commit*."""
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
        file_paths: set[str] = set()
        for path in self._tracked_paths():
            if cancel_check:
                cancel_check()
            if self._is_indexable_path(path):
                file_paths.add(path)
        commit_paths = {f"commits/{sha}.txt" for sha in self._commit_shas()}
        return {self._file_source: file_paths, self._commit_source: commit_paths}

    def current_commit(self) -> str | None:
        """Return the current HEAD SHA."""
        return self._git(["rev-parse", "HEAD"]).strip() or None

    def _tracked_paths(self) -> list[str]:
        output = self._git(["ls-files", "-z"])
        return [path for path in output.split("\0") if path]

    def _changed_paths(self, last_commit: str) -> list[str]:
        output = self._git(["diff", "--name-status", f"{last_commit}..HEAD", "--"])
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
        head = self.current_commit()
        entries: list[FileEntry] = []
        for path in paths:
            if cancel_check:
                cancel_check()
            entry = self._read_file_entry(path, head)
            if entry is not None:
                entries.append(entry)
        return entries

    def _read_file_entry(self, path: str, head: str | None) -> FileEntry | None:
        if not self._is_indexable_path(path):
            return None
        full_path = self.repo_path / Path(path)
        try:
            if not full_path.is_file() or full_path.stat().st_size > _MAX_FILE_BYTES:
                return None
            raw = full_path.read_bytes()
        except OSError:
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
            args.append(f"{last_commit}..HEAD")
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
        output = self._git(["log", "--format=%H"])
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
