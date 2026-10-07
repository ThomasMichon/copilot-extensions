"""``pr rounds``: how a PR's review/fix rounds are trending, and whether to stop.

A **round** is a head the reviewer reviewed: its latest review in effect on that head
(dismissed and pending reviews don't count). The round's **metric** is what that
review reported still to do -- open findings plus previously missed ones
(:func:`agent_worktrees.pr_bar.finding_counts`). A review whose summary states no
count is a round without a metric: it's excluded from the trend, never read as zero.

The **guard** is pure (:func:`guard`):

- ``done`` -- the latest round measured zero: nothing left to fix.
- ``plateau`` -- with at least ``plateau_passes + 1`` measured rounds, none of the last
  ``plateau_passes`` improved on the best (lowest) metric before them: the loop isn't
  converging, so stop and rethink (a lower-level fix, or a person) rather than run
  another round.
- ``round_cap`` -- ``max_rounds`` rounds have run without finishing.
- ``continue`` -- otherwise: "Round N of M".
- ``unknown`` -- the reviews couldn't be read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import pr_bar

DEFAULT_MAX_ROUNDS = 6
DEFAULT_PLATEAU_PASSES = 3
EXIT = {"done": 0, "continue": 0, "plateau": 20, "round_cap": 21, "unknown": 12}


@dataclass
class Round:
    head: str
    at: str
    open: int | None = None
    missed: int | None = None

    @property
    def metric(self) -> int | None:
        return None if self.open is None else self.open + (self.missed or 0)


@dataclass
class Guard:
    repo: str
    number: int
    round: int
    max_rounds: int
    plateau_passes: int
    verdict: str
    reason: str
    trend: list = field(default_factory=list)   # the measured metrics, oldest first
    rounds: list = field(default_factory=list)  # every round, oldest first

    def to_dict(self) -> dict:
        return asdict(self)


def rounds_of(snap: pr_bar.Snapshot, reviewer: str) -> list[Round]:
    """The reviewer's rounds, oldest first: one per head, from its latest review in
    effect on that head (ordered by that review)."""
    latest: dict[str, dict] = {}
    for r in sorted(snap.reviews, key=pr_bar._order):
        if ((r.get("author") or "").lower() == reviewer.lower() and r.get("state") in pr_bar._EFFECTIVE
                and r.get("commit")):
            latest[r["commit"]] = r
    out = []
    for head, r in sorted(latest.items(), key=lambda kv: pr_bar._order(kv[1])):
        counts = pr_bar.finding_counts(r.get("body") or "")
        out.append(Round(head=head, at=r.get("at") or "", open=counts[0] if counts else None,
                         missed=counts[1] if counts else None))
    return out


def guard(rounds: list[Round], *, max_rounds: int = DEFAULT_MAX_ROUNDS,
          plateau_passes: int = DEFAULT_PLATEAU_PASSES) -> tuple[str, str]:
    """``(verdict, reason)`` for *rounds* (oldest first); see the module docstring."""
    n = len(rounds)
    measured = [r.metric for r in rounds if r.metric is not None]
    if rounds and rounds[-1].metric == 0:
        return "done", f"round {n} reported nothing left to fix"
    if plateau_passes > 0 and len(measured) >= plateau_passes + 1:
        best_before, recent = min(measured[:-plateau_passes]), measured[-plateau_passes:]
        if min(recent) >= best_before:
            return "plateau", (f"no improvement in the last {plateau_passes} rounds (best before: "
                               f"{best_before}; since: {', '.join(map(str, recent))}) -- the loop isn't "
                               "converging: look for a lower-level fix, or bring in a person")
    if n >= max_rounds:
        return "round_cap", f"{n} rounds run, the cap is {max_rounds}, and findings remain"
    trend = f" (findings: {' -> '.join(map(str, measured))})" if measured else ""
    return "continue", f"round {n} of {max_rounds}: the stop condition isn't met yet{trend}"


def evaluate(snap: pr_bar.Snapshot, *, reviewer: str = pr_bar.COPILOT_REVIEWER,
             max_rounds: int = DEFAULT_MAX_ROUNDS, plateau_passes: int = DEFAULT_PLATEAU_PASSES) -> Guard:
    """The guard for one read of a PR -- pure, so recorded snapshots replay exactly."""
    if "pr" in snap.errors or "reviews" in snap.errors:
        why = snap.errors.get("reviews") or snap.errors.get("pr")
        return Guard(snap.repo, snap.number, 0, max_rounds, plateau_passes, "unknown", str(why))
    rounds = rounds_of(snap, reviewer)
    verdict, reason = guard(rounds, max_rounds=max_rounds, plateau_passes=plateau_passes)
    return Guard(snap.repo, snap.number, len(rounds), max_rounds, plateau_passes, verdict, reason,
                 trend=[r.metric for r in rounds if r.metric is not None],
                 rounds=[{**asdict(r), "metric": r.metric} for r in rounds])
