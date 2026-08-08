"""Drive-the-worktree-to-resolution -- the enforced clean-up half of a loop.

A worker that finishes a goal-loop -- whether the work **landed** or it is
**abandoning** the effort -- must leave its worktree in a *clean, resolved final
state*, never an orphan branch half-done nobody owns. This module packages that
mandate as an inspectable, executable :class:`ResolutionPlan`:

* **landed** -- the change merged; nothing to unwind. The only step is a
  *verify-clean* check that no uncommitted work was left behind.
* **abandoned** -- unwind the workspace to its base (a reset to the tracked
  upstream) so it reads clean and is prunable, then *reconcile the source* (the
  change/issue the worker was sent for) so nothing downstream believes the work
  landed.

The plan is **pure** -- planning never touches git, the network, or the queue.
The CLI (``agent-dispatch resolve``) turns a plan into action, and the abandon
path surfaces it so the required unwind is never a silent expectation. Splitting
*what to do* (here) from *doing it* (the CLI) keeps the invariant testable and
lets a caller preview the destructive unwind before it runs.

Whose hands do the git? A worker driving **its own** worktree to a clean state is
exactly what the vision expects, so ``resolve`` runs the minimal unwind on the
caller's own workspace. It stays a *coordination* verb: agent-dispatch packages
the steps and reconciles the source by **instruction**, it does not reach into
another worker's workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LANDED = "landed"
ABANDONED = "abandoned"

#: The outcomes a resolution can drive toward.
OUTCOMES: tuple[str, ...] = (LANDED, ABANDONED)


class ResolutionError(ValueError):
    """Raised for an unknown outcome (or otherwise un-plannable request)."""


@dataclass(frozen=True)
class ResolutionStep:
    """One step in driving a worktree to its resolved state.

    ``argv`` is the concrete command a caller runs to perform the step (executed
    in the caller's own worktree); an **advisory** step (``argv is None``) is an
    instruction the worker/operator carries out by hand -- the source-reconcile
    beat, which agent-dispatch coordinates rather than performs. ``destructive``
    flags a step that discards working-tree state, so the executor can gate it.
    """

    kind: str
    description: str
    argv: tuple[str, ...] | None = None
    destructive: bool = False

    @property
    def advisory(self) -> bool:
        return self.argv is None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "description": self.description,
            "argv": list(self.argv) if self.argv is not None else None,
            "destructive": self.destructive,
            "advisory": self.advisory,
        }


@dataclass(frozen=True)
class ResolutionPlan:
    """The ordered steps that drive a worktree to a clean, resolved final state."""

    outcome: str
    base_ref: str
    steps: tuple[ResolutionStep, ...]
    source_ref: str | None = None
    reason: str | None = None
    _extra: dict = field(default_factory=dict)

    @property
    def has_destructive_steps(self) -> bool:
        return any(s.destructive for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "base_ref": self.base_ref,
            "source_ref": self.source_ref,
            "reason": self.reason,
            "steps": [s.to_dict() for s in self.steps],
            "has_destructive_steps": self.has_destructive_steps,
        }


def base_ref(base: str | None) -> str:
    """The git ref an abandoned worktree unwinds onto.

    An explicit ``base`` names the branch on the canonical remote (``origin/<base>``
    -- the tracked upstream base a reset makes lossless). With no base, fall back
    to the branch's own tracked upstream (``@{upstream}``), which is what a
    worktree branch is set up to track.
    """
    if base:
        base = base.strip()
    if not base:
        return "@{upstream}"
    # Already-qualified refs (a remote-tracking name or an explicit ref) pass
    # through; a bare branch name is resolved against the canonical remote.
    if base.startswith(("origin/", "refs/")) or base == "@{upstream}":
        return base
    return f"origin/{base}"


def plan_resolution(
    outcome: str,
    *,
    base: str | None = None,
    source_ref: str | None = None,
    reason: str | None = None,
) -> ResolutionPlan:
    """Build the :class:`ResolutionPlan` for ``outcome``.

    ``base`` is the branch an abandoned worktree unwinds onto (default: the
    branch's tracked upstream). ``source_ref`` names the change/issue the worker
    was driving, folded into the source-reconcile instruction. ``reason`` is the
    abandonment reason, recorded on the plan and its instruction.
    """
    if outcome not in OUTCOMES:
        known = ", ".join(OUTCOMES)
        raise ResolutionError(f"unknown outcome {outcome!r}; known: {known}")

    ref = base_ref(base)

    if outcome == LANDED:
        steps = (
            ResolutionStep(
                kind="verify-clean",
                description=(
                    "Confirm the worktree carries no uncommitted work -- the change "
                    "landed, so the workspace should already be clean."
                ),
                argv=("git", "status", "--porcelain"),
            ),
        )
        return ResolutionPlan(
            outcome=outcome, base_ref=ref, steps=steps, source_ref=source_ref, reason=reason
        )

    # ABANDONED: unwind to base, drop untracked cruft, then reconcile the source.
    what = source_ref or "the change/issue you were sent for"
    reconcile = (
        f"Reconcile the source ({what}): notify the producing domain (effort, issue, "
        f"or PR) that this work was abandoned so its records stop believing it landed."
    )
    if reason:
        reconcile += f" Reason: {reason}."
    steps = (
        ResolutionStep(
            kind="unwind-to-base",
            description=(
                f"Reset the worktree branch to its base ({ref}) so nothing is left "
                f"half-done and the workspace is prunable."
            ),
            argv=("git", "reset", "--hard", ref),
            destructive=True,
        ),
        ResolutionStep(
            kind="drop-untracked",
            description="Remove untracked files/dirs left behind so the tree reads clean.",
            argv=("git", "clean", "-fd"),
            destructive=True,
        ),
        ResolutionStep(
            kind="reconcile-source",
            description=reconcile,
            argv=None,
        ),
    )
    return ResolutionPlan(
        outcome=outcome, base_ref=ref, steps=steps, source_ref=source_ref, reason=reason
    )
