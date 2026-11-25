"""Project the composed promotion index onto Main's consumption-side release scope.

``tools/promotion-release-scope.json`` is the reviewed policy. Its
``release_roots`` are the only top-level entries that may reach Main, so new
top-level content stays on Dev until a reviewer classifies it. Its ``excluded``
paths are removed even inside a release root.

``tools/promotion-release-scope-patterns.json`` is a separate reviewed policy
whose ``excluded_patterns`` remove every path under a matching prefix, where
``*`` stands for exactly one path segment. They name repeated dev-only shapes
such as the tests inside vendored library copies and never touch the
Main-owned ``.github`` control. The patterns live in their own file so the
primary policy keeps its v1 shape: an installed Manager whose packaged reader
predates patterns still composes newer source, it just keeps those paths.

Dev keeps everything; only the promoted tree changes. A tree without the
primary policy file (an isolated fixture) is left unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

POLICY_PATH = "tools/promotion-release-scope.json"
SCHEMA = "copilot-extensions.promotion-release-scope/v1"
PATTERNS_PATH = "tools/promotion-release-scope-patterns.json"
PATTERNS_SCHEMA = "copilot-extensions.promotion-release-scope-patterns/v1"


class ReleaseScopeError(ValueError):
    """The release-scope policy is missing a required field or is ambiguous."""


@dataclass(frozen=True)
class ReleaseScope:
    release_roots: frozenset[str]
    excluded: tuple[str, ...]
    excluded_patterns: tuple[tuple[str, ...], ...] = ()

    def released(self, path: str) -> bool:
        """Return whether one tracked path belongs on Main."""
        if path.split("/", 1)[0] not in self.release_roots:
            return False
        if any(path == prefix or path.startswith(prefix + "/") for prefix in self.excluded):
            return False
        parts = path.split("/")
        if parts[0] == ".github":
            return True
        return not any(_matches(pattern, parts) for pattern in self.excluded_patterns)

    def unclassified(self, paths: Iterable[str]) -> list[str]:
        """Top-level entries that are neither released nor explicitly excluded."""
        roots = {path.split("/", 1)[0] for path in paths}
        return sorted(roots - self.release_roots - set(self.excluded))


def _matches(pattern: tuple[str, ...], parts: list[str]) -> bool:
    return len(parts) >= len(pattern) and all(
        want == "*" or want == part for want, part in zip(pattern, parts))


def _pattern(value: str) -> tuple[str, ...]:
    parts = tuple(_relative(value).split("/"))
    if any("*" in part and part != "*" for part in parts):
        raise ReleaseScopeError(f"a pattern wildcard must be a whole path segment: {value}")
    if parts[-1] == "*":
        raise ReleaseScopeError(f"a pattern must end in a literal segment: {value}")
    if parts[0] == ".github":
        raise ReleaseScopeError(f"Main-owned control paths cannot be excluded: {value}")
    return parts


def _relative(value: object) -> str:
    if (
        not isinstance(value, str) or not value or "\\" in value or ":" in value
        or value.startswith("/") or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ReleaseScopeError(f"release scope path is not repository-relative: {value!r}")
    return PurePosixPath(value).as_posix()


def parse_scope(data: object) -> ReleaseScope:
    if not isinstance(data, dict) or set(data) != {"schema", "release_roots", "excluded"}:
        raise ReleaseScopeError("release scope requires exactly schema, release_roots and excluded")
    if data["schema"] != SCHEMA:
        raise ReleaseScopeError(f"unsupported release scope schema: {data['schema']!r}")
    roots, excluded = data["release_roots"], data["excluded"]
    if not isinstance(roots, list) or not roots or not isinstance(excluded, dict):
        raise ReleaseScopeError("release_roots must be a non-empty list and excluded an object")
    names = [_relative(root) for root in roots]
    if len(set(names)) != len(names) or any("/" in name for name in names):
        raise ReleaseScopeError("release_roots must be unique top-level names")
    prefixes = [_relative(path) for path in excluded]
    if any(not isinstance(reason, str) or not reason.strip() for reason in excluded.values()):
        raise ReleaseScopeError("every excluded path needs a review reason")
    for prefix in prefixes:
        if prefix in names:
            raise ReleaseScopeError(f"path is both released and excluded: {prefix}")
        if prefix.split("/", 1)[0] == ".github":
            raise ReleaseScopeError(f"Main-owned control paths cannot be excluded: {prefix}")
    return ReleaseScope(frozenset(names), tuple(sorted(prefixes)))


def parse_patterns(data: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(data, dict) or set(data) != {"schema", "excluded_patterns"}:
        raise ReleaseScopeError("release scope patterns require exactly schema and excluded_patterns")
    if data["schema"] != PATTERNS_SCHEMA:
        raise ReleaseScopeError(f"unsupported release scope patterns schema: {data['schema']!r}")
    patterns = data["excluded_patterns"]
    if not isinstance(patterns, dict):
        raise ReleaseScopeError("excluded_patterns must be an object")
    if any(not isinstance(reason, str) or not reason.strip() for reason in patterns.values()):
        raise ReleaseScopeError("every excluded pattern needs a review reason")
    return tuple(sorted(_pattern(pattern) for pattern in patterns))


def load_scope(root: Path) -> ReleaseScope | None:
    path = root / POLICY_PATH
    if not path.is_file():
        return None
    scope = parse_scope(json.loads(path.read_text(encoding="utf-8")))
    patterns = root / PATTERNS_PATH
    if not patterns.is_file():
        return scope
    return replace(scope, excluded_patterns=parse_patterns(
        json.loads(patterns.read_text(encoding="utf-8"))))


def removed_paths(scope: ReleaseScope, tracked: Iterable[str]) -> list[str]:
    return sorted(path for path in tracked if not scope.released(path))


def project_index(root: Path, git: Callable[..., str]) -> list[str]:
    """Drop out-of-scope entries from ``root``'s index; return the removed paths.

    ``git(args, cwd=..., input_text=...)`` is the caller's Git runner. The
    working tree keeps every file, so a later ``git add -A`` must be followed
    by another projection before ``write-tree``.
    """
    scope = load_scope(root)
    if scope is None:
        return []
    listing = git(["ls-files", "-z", "--cached"], cwd=root)
    removed = removed_paths(scope, [path for path in listing.split("\0") if path])
    if removed:
        git(
            ["update-index", "-z", "--force-remove", "--stdin"], cwd=root,
            input_text="".join(f"{path}\0" for path in removed),
        )
    return removed
