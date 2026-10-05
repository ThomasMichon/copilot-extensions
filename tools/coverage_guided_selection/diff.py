"""Compute a PR's own changed lines for `decide()`'s `changed_lines` param.

Walks every file touched between `base_ref` and `head_ref` (optionally
restricted to `path_prefix` -- e.g. a single plugin's own `src/` tree, so
the result's keys line up with the baseline's own relative-path keys,
which only ever cover `cov_source`) and returns, per file, the set of
NEW-file line numbers `selection.select_tests` should treat as "touched":

- Every line in an added/modified hunk's own new range.
- For a pure-deletion hunk (nothing added in its place), the single
  surviving line immediately after the deletion point -- there is no new
  line to blame for the deleted code itself, but that adjacent line is the
  nearest place a test's own coverage can still be checked against.

Deliberately simple relative to `ancestor_resolution.py`'s own remap
logic: that module translates OLD attribution forward through intervening
history (baseline commit -> fork point); this one only asks "what did
THIS diff touch" (base ref -> head ref), with no baseline or attribution
involved. Reuses that module's own private `_git`/`_parse_hunks` git
plumbing -- both modules are the same package's own internal collaborators,
never a cross-plugin import.
"""

from __future__ import annotations

from pathlib import Path

try:
    from ancestor_resolution import _git, _parse_hunks
except ModuleNotFoundError:
    from tools.coverage_guided_selection.ancestor_resolution import _git, _parse_hunks


def changed_files(
    repo_root: Path, base_ref: str, head_ref: str, *, path_prefix: str | None = None,
) -> list[str]:
    """Repository-relative paths of every file that differs between
    `base_ref` and `head_ref`, optionally restricted to `path_prefix`."""
    args = ["diff", "--name-only", base_ref, head_ref]
    if path_prefix:
        args += ["--", path_prefix]
    output = _git(args, cwd=repo_root)
    return [line.strip() for line in output.splitlines() if line.strip()]


def compute_changed_lines(
    repo_root: Path, base_ref: str, head_ref: str, *, path_prefix: str | None = None,
) -> dict[str, list[int]]:
    """The same shape `selection.select_tests`/`decide()` take as
    `changed_lines`: file -> sorted touched new-file line numbers.

    A file with a nonempty diff but no parsed `@@` hunk at all (a binary
    file, or any unified-diff shape `_parse_hunks` doesn't recognize) is
    skipped entirely -- there is nothing line-level to select on, and
    `selection.select_tests` only ever reasons about files this dict
    actually names, so omitting it here is equivalent to "not part of this
    diff" for selection purposes, never a silent false negative the way
    returning a wrong/empty line list under a real key would be.
    """
    result: dict[str, list[int]] = {}
    for file in changed_files(repo_root, base_ref, head_ref, path_prefix=path_prefix):
        diff_text = _git(
            ["diff", "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv",
             base_ref, head_ref, "--", file],
            cwd=repo_root,
        )
        hunks = _parse_hunks(diff_text)
        if not hunks:
            continue
        lines: set[int] = set()
        for _old_start, _old_len, new_start, new_len in hunks:
            if new_len > 0:
                lines.update(range(new_start, new_start + new_len))
            else:
                lines.add(max(new_start, 1))
        if lines:
            result[file] = sorted(lines)
    return result
