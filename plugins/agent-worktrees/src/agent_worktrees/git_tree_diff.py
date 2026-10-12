"""Batched object identity comparisons for the classifier's tree fallback."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

_RAW_HEADER = re.compile(
    r":[0-7]{6} [0-7]{6} ([0-9a-f]{40}|[0-9a-f]{64}) "
    r"([0-9a-f]{40}|[0-9a-f]{64}) [ADMT]"
)


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
