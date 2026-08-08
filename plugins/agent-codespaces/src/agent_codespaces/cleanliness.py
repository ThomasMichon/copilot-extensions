"""CodeSpace at-rest cleanliness predicate + probe (resource-obligation-settlement Ph3b).

A worktree answers for a CodeSpace it borrowed before it may finalize
(agent-fabric ``resource-accountability``). A CodeSpace reaches **at-rest** when
its *work is safe* -- merged or off-box -- **not** when it is deleted (an at-rest
CodeSpace may keep running, its claim released for the next borrower). This
module decides that: given a CodeSpace's git state (uncommitted changes, unpushed
commits, branches with commits not on their upstream) and whether a dispatch is
still in-flight, is the CodeSpace **safe to settle**?

Like ``fence.py`` this is pure decision logic + a shell probe builder + a
defensive parser; the caller runs the probe over the existing SSH channel and
settles the obligation (via ``agent-worktrees claims settle`` / the lease
``--disposition`` mirror) only on a *definitive* at-rest verdict. **Conservative
by construction:** anything the probe cannot determine reads as **not** at-rest,
so an un-probeable CodeSpace stays an active (blocking) obligation rather than
being settled blind.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

#: Marker lines the probe emits (KEY=VALUE), parsed back into GitCleanliness.
_MARK_KNOWN = "OBLIGATION_PROBE"
_MARK_DIRTY = "DIRTY"
_MARK_AHEAD = "AHEAD"
_MARK_UNPUSHED_BRANCHES = "UNPUSHED_BRANCHES"


@dataclass(frozen=True)
class GitCleanliness:
    """A CodeSpace workspace's git safety signals (from the remote probe).

    ``known`` is False when the probe could not evaluate the repo (no workspace
    repo found, git error, unparseable output) -- the conservative "cannot prove
    safe" signal. ``dirty`` = uncommitted changes in **any** workspace repo;
    ``ahead`` = commits reachable from HEAD that exist on **no remote** (summed
    across repos -- local-only work on the checked-out line, well-defined even
    with no upstream); ``unpushed_branches`` = local branches (across repos)
    carrying commits on no remote (work parked off the pushed line).
    """

    known: bool = False
    dirty: bool = True
    ahead: int = 0
    unpushed_branches: int = 0


def is_git_clean(gc: GitCleanliness) -> bool:
    """True when the workspace's git state is definitively safe (nothing owed).

    Requires a **known** verdict plus no uncommitted changes, no unpushed HEAD
    commits, and no branches with unpushed commits. An unknown verdict is never
    clean.
    """
    return (
        gc.known
        and not gc.dirty
        and gc.ahead <= 0
        and gc.unpushed_branches <= 0
    )


def at_rest(gc: GitCleanliness, *, in_flight: bool) -> bool:
    """True when the CodeSpace is safe to settle to ``at-rest``.

    The work is off-box/merged (``is_git_clean``) **and** no dispatch is still
    driving it (``in_flight`` is host-side knowledge, not from the probe). A
    conservative AND -- either signal unmet keeps the obligation active.
    """
    return is_git_clean(gc) and not in_flight


def probe_command(workspace_glob: str = "/workspaces/*") -> str:
    """Shell command (run inside the CodeSpace) that emits cleanliness signals.

    Scans **every** git repo under ``workspace_glob`` (not just the first --
    a borrowed CodeSpace typically holds both the scaffold repo and the actual
    work repo, and unpushed work in *any* of them keeps the CodeSpace unsafe)
    and prints ``OBLIGATION_PROBE=1`` plus the aggregated ``DIRTY`` /
    ``AHEAD`` / ``UNPUSHED_BRANCHES`` KEY=VALUE lines:

    * ``DIRTY``   -- ``1`` if **any** repo has uncommitted changes.
    * ``AHEAD``   -- total commits reachable from **HEAD** that exist on **no
      remote** (``git rev-list --count HEAD --not --remotes``), summed across
      repos. This is the accountability-correct "local-only work on the checked-
      out line" -- unlike ``@{u}..HEAD`` it is well-defined even when the branch
      has **no upstream** (a common CodeSpace state), where ``@{u}`` errors to 0
      and would falsely read as clean.
    * ``UNPUSHED_BRANCHES`` -- count of local branches (across all repos) that
      carry commits on no remote (``<branch> --not --remotes`` > 0) -- work
      parked off the pushed line.

    Emits nothing when no repo is found (the parser then degrades to
    ``known=False``). Read-only: never mutates a repo.

    ``workspace_glob`` is interpolated **unquoted** so the shell expands ``*``
    (``shopt -s nullglob`` makes an unmatched glob vanish rather than pass a
    literal). It is a trusted, code-supplied value (the default or an internal
    override), never user input -- do **not** ``shlex.quote`` it, or the glob is
    single-quoted and never expands (which silently disabled the probe on every
    real CodeSpace).
    """
    # A single defensive bash pipeline: enumerate every repo, aggregate signals.
    inner = (
        'shopt -s nullglob 2>/dev/null; '
        'found=0; dirty=0; ahead=0; nbr=0; '
        f'for g in {workspace_glob}/.git; do '
        'd=$(dirname "$g"); '
        'git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || continue; '
        'found=1; '
        '[ -n "$(git -C "$d" status --porcelain 2>/dev/null)" ] && dirty=1; '
        'a=$(git -C "$d" rev-list --count HEAD --not --remotes 2>/dev/null || echo 0); '
        'ahead=$((ahead + ${a:-0})); '
        'for b in $(git -C "$d" for-each-ref --format="%(refname:short)" '
        'refs/heads 2>/dev/null); do '
        'c=$(git -C "$d" rev-list --count "$b" --not --remotes 2>/dev/null || echo 0); '
        '[ "${c:-0}" -gt 0 ] && nbr=$((nbr+1)); done; '
        'done; '
        '[ "$found" = 0 ] && exit 0; '
        'echo "' + _MARK_KNOWN + '=1"; '
        'echo "' + _MARK_DIRTY + '=$dirty"; '
        'echo "' + _MARK_AHEAD + '=$ahead"; '
        'echo "' + _MARK_UNPUSHED_BRANCHES + '=$nbr"'
    )
    return "bash -lc " + shlex.quote(inner)


def _int(value: str) -> int:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return 0


def parse_probe(output: str | None) -> GitCleanliness:
    """Parse the probe's KEY=VALUE output into a :class:`GitCleanliness`.

    Requires the ``OBLIGATION_PROBE=1`` marker to trust the result; without it
    (empty output, no repo found, garbage) returns ``known=False`` (conservative
    -- not clean). Never raises.
    """
    if not output:
        return GitCleanliness(known=False)
    values: dict[str, str] = {}
    for line in output.splitlines():
        m = re.match(r"\s*([A-Z_]+)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2)
    if values.get(_MARK_KNOWN) != "1":
        return GitCleanliness(known=False)
    return GitCleanliness(
        known=True,
        dirty=values.get(_MARK_DIRTY, "1") != "0",
        ahead=_int(values.get(_MARK_AHEAD, "0")),
        unpushed_branches=_int(values.get(_MARK_UNPUSHED_BRANCHES, "0")),
    )


async def probe_cleanliness(
    manager: object,
    name: str,
    *,
    workspace_glob: str = "/workspaces/*",
    timeout: float = 30.0,
) -> GitCleanliness:
    """Run the cleanliness probe inside CodeSpace ``name`` over an SSH channel.

    ``manager`` is any object exposing ``async exec_command(name, cmd, timeout=)``
    (the agent-codespaces ``ConnectionManager``). Returns the parsed
    :class:`GitCleanliness`; **degrade-safe** -- any exec failure / nonzero exit
    yields ``known=False`` (conservative: not clean), never raises. The caller
    combines it with host-side ``in_flight`` via :func:`at_rest` before settling.
    """
    try:
        result = await manager.exec_command(
            name, probe_command(workspace_glob), timeout=timeout,
        )
    except Exception:
        return GitCleanliness(known=False)
    text = result.stdout if getattr(result, "exit_code", 1) == 0 else ""
    return parse_probe(text)
