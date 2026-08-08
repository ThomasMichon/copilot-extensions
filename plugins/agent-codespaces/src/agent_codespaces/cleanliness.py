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
    safe" signal. ``dirty`` = uncommitted changes; ``ahead`` = unpushed commits on
    HEAD vs its upstream; ``unpushed_branches`` = local branches carrying commits
    not on their upstream (work parked off the pushed line).
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

    Discovers the first git repo under ``workspace_glob`` and prints
    ``OBLIGATION_PROBE=1`` plus ``DIRTY``/``AHEAD``/``UNPUSHED_BRANCHES`` as
    KEY=VALUE lines. Swallows all errors and prints nothing on failure (so the
    parser degrades to ``known=False``). Read-only: never mutates the repo.
    """
    glob = shlex.quote(workspace_glob)
    # A single defensive bash pipeline: find a repo, compute the three signals.
    return (
        "bash -lc " + shlex.quote(
            'set -o pipefail 2>/dev/null; '
            f'd=$(for g in {glob}/.git; do [ -e "$g" ] && dirname "$g" && break; done); '
            '[ -z "$d" ] && exit 0; '
            'cd "$d" || exit 0; '
            'echo "' + _MARK_KNOWN + '=1"; '
            'if [ -n "$(git status --porcelain 2>/dev/null)" ]; then '
            'echo "' + _MARK_DIRTY + '=1"; else echo "' + _MARK_DIRTY + '=0"; fi; '
            'a=$(git rev-list --count "@{u}..HEAD" 2>/dev/null || echo 0); '
            'echo "' + _MARK_AHEAD + '=${a:-0}"; '
            'n=0; '
            'for b in $(git for-each-ref --format="%(refname:short)" refs/heads 2>/dev/null); do '
            'c=$(git rev-list --count "$b@{u}..$b" 2>/dev/null || echo 0); '
            '[ "${c:-0}" -gt 0 ] && n=$((n+1)); done; '
            'echo "' + _MARK_UNPUSHED_BRANCHES + '=$n"'
        )
    )


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
