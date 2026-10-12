"""Batched object identity comparisons for the classifier's tree fallback."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable, Iterator

_RAW_HEADER = re.compile(
    r":[0-7]{6} [0-7]{6} ([0-9a-f]{40}|[0-9a-f]{64}) "
    r"([0-9a-f]{40}|[0-9a-f]{64}) [ADMT]"
)
_TREE_ENTRY = re.compile(
    r"[0-7]{6} (?:blob|tree|commit) ([0-9a-f]{40}|[0-9a-f]{64})\t(.+)",
    re.DOTALL,
)
_MAX_ARG_UNITS = 24 * 1024


class TreeDiffError(ValueError):
    """A tree comparison could not provide complete object evidence."""


def read(
    run_git: Callable[..., subprocess.CompletedProcess[str]],
    before: str,
    after: str,
) -> dict[str, tuple[str, str]]:
    """Read full object IDs keyed by literal, NUL-delimited repository paths.

    Rename detection is disabled so a move retains both its deletion and
    addition. Modes remain in the changed-path set, but callers compare object
    IDs, preserving the classifier's blob/gitlink rather than mode semantics.
    """
    result = run_git(
        "diff", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
        "--no-textconv", "--ignore-submodules=none", "--no-relative", "-z",
        before, after, "--",
    )
    if result.returncode != 0:
        raise TreeDiffError(f"git diff failed (rc={result.returncode}): {result.stderr.strip()}")
    return parse(result.stdout)


def parse(output: str) -> dict[str, tuple[str, str]]:
    """Reject incomplete/ambiguous raw output rather than infer a clean tree."""
    if not output:
        return {}
    if not output.endswith("\0"):
        raise TreeDiffError("unterminated raw tree diff")
    fields = output[:-1].split("\0")
    if len(fields) % 2:
        raise TreeDiffError("incomplete raw tree diff")
    changes: dict[str, tuple[str, str]] = {}
    for index in range(0, len(fields), 2):
        header, path = fields[index:index + 2]
        match = _RAW_HEADER.fullmatch(header)
        if match is None or not path or path in changes:
            raise TreeDiffError("invalid or duplicate raw tree diff entry")
        before, after = match.groups()
        if len(before) != len(after):
            raise TreeDiffError("mixed object formats in raw tree diff")
        changes[path] = before, after
    return changes


def _argument_cost(argument: str) -> int:
    quoted = subprocess.list2cmdline([argument])
    return max(
        len(quoted.encode("utf-16-le")) // 2,
        len(argument.encode("utf-8")) + 8,
    ) + 1


def _path_batches(
    arguments: tuple[str, ...], paths: Iterable[str],
) -> Iterator[tuple[str, ...]]:
    overhead = sum(_argument_cost(argument) for argument in ("git", *arguments))
    batch: list[str] = []
    selected: set[str] = set()
    cost = overhead
    for path in sorted(paths):
        argument = f":(literal){path}"
        size = _argument_cost(argument)
        if overhead + size > _MAX_ARG_UNITS:
            raise TreeDiffError("tree lookup path exceeds the safe argument budget")
        # ls-tree descends into a requested directory when the same request
        # also names a descendant. Keep those requests separate: each must
        # return only the exact entry, not unrelated children under that tree.
        parents = path.split("/")
        overlaps = any("/".join(parents[:index]) in selected for index in range(1, len(parents)))
        if batch and (cost + size > _MAX_ARG_UNITS or overlaps):
            yield tuple(batch)
            batch = []
            selected = set()
            cost = overhead
        batch.append(path)
        selected.add(path)
        cost += size
    if batch:
        yield tuple(batch)


def read_objects(
    run_git: Callable[..., subprocess.CompletedProcess[str]],
    tree: str,
    paths: Iterable[str],
) -> dict[str, str]:
    """Look up exact upstream entries without enumerating unrelated changes.

    Nonrecursive ls-tree returns blobs, gitlinks and directory objects alike.
    Missing entries are absent from the map. Literal pathspecs, NUL output
    and bounded non-overlapping argv batches also work on older Git versions;
    no stdin protocol, new cat-file flags or helper process is required.
    """
    arguments = ("ls-tree", "--full-tree", "-z", tree, "--")
    objects: dict[str, str] = {}
    for batch in _path_batches(arguments, paths):
        result = run_git(*arguments, *(f":(literal){path}" for path in batch))
        if result.returncode != 0:
            raise TreeDiffError(
                f"git ls-tree failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        if result.stdout and not result.stdout.endswith("\0"):
            raise TreeDiffError("unterminated tree lookup")
        requested = set(batch)
        for entry in result.stdout.split("\0")[:-1]:
            match = _TREE_ENTRY.fullmatch(entry)
            if match is None:
                raise TreeDiffError("invalid tree lookup entry")
            object_id, path = match.groups()
            if path not in requested or path in objects:
                raise TreeDiffError("unexpected or duplicate tree lookup entry")
            objects[path] = object_id
    return objects
