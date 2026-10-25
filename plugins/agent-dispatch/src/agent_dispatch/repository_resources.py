"""Optional registered-repository resources, resolved only at their use boundary."""

from __future__ import annotations

import json
import os
import posixpath
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import monotonic
from typing import TypeGuard

from .repository_resource_capture import run_agent_worktrees_capture
from .registrar import RegistrarError

_PREFIX = "repo:"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def is_repository_resource(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.startswith(_PREFIX)


def split_repository_resource(value: str) -> tuple[str, str]:
    name, separator, relative = value[len(_PREFIX):].partition(":")
    if not value.startswith(_PREFIX) or not _NAME.fullmatch(name) or not separator:
        raise RegistrarError("repository resource must be repo:<registered-name>:<relative-path>")
    posix = PurePosixPath(relative.replace("\\", "/"))
    windows = PureWindowsPath(relative)
    if (
        not relative or posix.is_absolute() or windows.anchor
        or ".." in posix.parts or "\0" in relative
    ):
        raise RegistrarError(
            f"repository resource {value!r} must stay within its registered root"
        )
    return name, str(posix)


def validate_resource_arguments(values: Sequence[object]) -> None:
    """Validate named-resource syntax without registry or filesystem access."""
    for value in values:
        if is_repository_resource(value):
            split_repository_resource(value)


def registered_repository_root(
    name: str, *, execution: bool = False, timeout: float = 15,
) -> Path:
    root, repo_class = _registered_repository(name, timeout=timeout)
    _check_execution_class(name, repo_class, execution=execution)
    return root


def _check_execution_class(name: str, repo_class: str, *, execution: bool) -> None:
    if execution and repo_class in {"reference", "knowledge"}:
        raise RegistrarError(
            f"registered repository {name!r} is {repo_class}-only; execution is refused"
        )


def _registered_repository(name: str, *, timeout: float) -> tuple[Path, str]:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise RegistrarError("registered repository name must use letters, digits, '.', '_' or '-'")
    result = run_agent_worktrees_capture(
        "repos", "find", name, "--registered-only", "--json", timeout=timeout
    )
    if result is None:
        raise RegistrarError(
            f"registered repository {name!r} resolution is unavailable or timed out; "
            "the agent-worktrees registry integration is required"
        )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[:1000]
        raise RegistrarError(f"registered repository {name!r} resolution failed: {detail}")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RegistrarError("registered repository resolution returned invalid JSON") from exc
    if not isinstance(data, dict) or data.get("registration") != "ready":
        raise RegistrarError(
            "agent-worktrees lacks the required registered-only repository resolution contract"
        )
    if (
        data.get("name") != name or not isinstance(data.get("class"), str)
        or data["class"] not in {"reference", "singleton", "worktree", "knowledge"}
    ):
        raise RegistrarError("registered repository resolution returned an invalid identity or class")
    raw = data.get("path")
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise RegistrarError("registered repository resolution did not return an absolute local root")
    try:
        root = Path(raw).resolve(strict=True)
        if not root.is_dir():
            raise RegistrarError(f"registered repository {name!r} local root is not a directory")
    except (OSError, RuntimeError, ValueError) as exc:
        raise RegistrarError(f"registered repository {name!r} local root is unavailable: {exc}") from exc
    return root, data["class"]


def relative_repository_resource(directory: str, relative: str) -> str:
    """Retain the registry authority of a path derived from a named directory."""
    name, base = split_repository_resource(directory)
    relative = relative.replace("\\", "/")
    value = f"repo:{name}:{posixpath.normpath(posixpath.join(base, relative))}"
    split_repository_resource(value)
    return value


def resolve_repository_resource(
    value: str, *, execution: bool = False, directory: bool = False,
) -> Path:
    name, relative = split_repository_resource(value)
    root = registered_repository_root(name, execution=execution)
    resource = _resource_at_root(value, root, relative)
    if directory and not resource.is_dir():
        raise RegistrarError(f"repository resource {value!r} must be a directory")
    return resource


def _resource_at_root(value: str, root: Path, relative: str) -> Path:
    try:
        candidate = (root / relative).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RegistrarError(
            f"repository resource {value!r} is unavailable or escapes its registered root: {exc}"
        ) from exc
    return candidate


def resolve_resource_argument(
    value: str, *, execution: bool = False, directory: bool = False,
) -> str:
    if not is_repository_resource(value):
        return value
    return str(resolve_repository_resource(value, execution=execution, directory=directory))


class RepositoryResourceSnapshot:
    """One operation's transient roots and shared registry lookup deadline."""

    def __init__(self, *, execution: bool = True):
        self._execution = execution
        self._roots: dict[str, tuple[Path, str]] = {}
        self._errors: dict[str, str] = {}
        self._deadline = monotonic() + 15

    def with_execution(self, *, execution: bool) -> RepositoryResourceSnapshot:
        """Share this operation's cache/deadline, not its admission mode."""
        view = RepositoryResourceSnapshot(execution=execution)
        view._roots = self._roots
        view._errors = self._errors
        view._deadline = self._deadline
        return view

    def root(self, name: str, *, execution: bool | None = None) -> Path:
        if name in self._errors:
            raise RegistrarError(self._errors[name])
        if name not in self._roots:
            remaining = self._deadline - monotonic()
            if remaining <= 0:
                raise RegistrarError("repository resource resolution exceeded its shared 15s budget")
            try:
                self._roots[name] = _registered_repository(name, timeout=remaining)
            except RegistrarError as exc:
                self._errors[name] = str(exc)
                raise
        root, repo_class = self._roots[name]
        _check_execution_class(
            name, repo_class, execution=self._execution if execution is None else execution,
        )
        return root

    def resolve(
        self, value: str, *, directory: bool = False, execution: bool | None = None,
    ) -> str:
        if not is_repository_resource(value):
            return value
        name, relative = split_repository_resource(value)
        path = _resource_at_root(value, self.root(name, execution=execution), relative)
        if directory and not path.is_dir():
            raise RegistrarError(f"repository resource {value!r} must be a directory")
        return str(path)

    def is_read_only(self, name: str) -> bool:
        self.root(name, execution=False)
        return self._roots[name][1] in {"reference", "knowledge"}


def execution_root(
    repository: object, cwd: str | Path | None, *,
    snapshot: RepositoryResourceSnapshot | None = None,
    resolve_resources: bool = True,
) -> str | Path | None:
    snapshot = snapshot or RepositoryResourceSnapshot()
    if repository is not None:
        if not isinstance(repository, str) or not _NAME.fullmatch(repository):
            raise RegistrarError("execution_repo must be a registered repository name")
    if isinstance(cwd, str) and is_repository_resource(cwd):
        split_repository_resource(cwd)
    if not resolve_resources:
        return cwd
    if repository is not None:
        return snapshot.root(repository, execution=True)
    if isinstance(cwd, str) and is_repository_resource(cwd):
        return snapshot.resolve(cwd, directory=True, execution=True)
    return cwd


def resolve_execution_resources(
    argv: Sequence[str], cwd: str | None, *,
    snapshot: RepositoryResourceSnapshot | None = None,
    bind_relative_executable: bool = False,
) -> tuple[list[str], str | None]:
    """Resolve source arguments separately from an explicitly named execution cwd."""
    snapshot = snapshot or RepositoryResourceSnapshot()
    resolved_cwd = snapshot.resolve(cwd, directory=True, execution=True) if cwd is not None else None
    # Without an independently fenced cwd, preserve the legacy refusal to
    # execute resources from a read-only repository in an implicit context.
    source_only = is_repository_resource(cwd)
    resolved_argv = [snapshot.resolve(part, execution=not source_only) for part in argv]
    if bind_relative_executable and resolved_argv and resolved_cwd and not Path(resolved_argv[0]).is_absolute() and any(
        separator in resolved_argv[0] for separator in (os.sep, os.altsep) if separator
    ):
        resolved_argv[0] = str(Path.cwd() / resolved_cwd / resolved_argv[0])
    return resolved_argv, resolved_cwd
