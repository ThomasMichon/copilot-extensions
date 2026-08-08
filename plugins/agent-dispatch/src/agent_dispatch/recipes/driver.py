"""The loop driver -- the executable rhythm that carries a recipe worker from
*start* to *resolution*.

A :class:`~agent_dispatch.recipes.registry.Recipe` declares the *shape* of a loop
(its charter, the domain events it **suspends** on, the **resolution** it drives
toward). The driver is the small state machine that turns that declaration into a
sequence of concrete next-actions as the world moves:

* **work** -- do a pass (review the change, rebase, advance the goal). The agent
  performs this; the layer only says "now is the time".
* **suspend** -- there is nothing to do until the world moves. Hand the wait to
  the layer (*hibernate-the-wait*) until one of the recipe's ``suspend_on`` events
  fires; the worker costs nothing meanwhile.
* **resolve** -- a terminal signal arrived (the change merged, or it was
  abandoned/closed). Drive the worktree to its clean resolved state
  (*drive-the-worktree-to-resolution*) and finish.

The decision is **pure**: :func:`decide` maps ``(recipe, signal)`` to a
:class:`DriverAction`, composing the recipe's own ``suspend_on`` + ``resolution``
with the hibernation and resolve substrate. Executing an action (spawning the
detached waiter, running ``resolve``) is the CLI's job -- keeping the rhythm
testable and letting a caller preview the next step.
"""

from __future__ import annotations

from dataclasses import dataclass

from .registry import Recipe
from ..resolution import ABANDONED, LANDED

# Action kinds.
WORK = "work"
SUSPEND = "suspend"
RESOLVE = "resolve"

# The synthetic signal a fresh loop starts from.
START = "start"
# The signal a caller sends after a work pass that did not resolve anything.
WORK_DONE = "work-done"
# An explicit "nothing changed / woke with no news" signal.
IDLE = "idle"

# Terminal signals, normalized to a resolution outcome. These are the generic
# names a domain emitter maps its world onto (a merged PR, a met goal, ...).
_LANDED_SIGNALS = frozenset({"merged", "landed", "goal-met", "resolved", "done"})
_ABANDONED_SIGNALS = frozenset({"abandoned", "closed", "goal-abandoned", "rejected"})


@dataclass(frozen=True)
class DriverAction:
    """The next step the driver prescribes.

    ``kind`` is :data:`WORK`, :data:`SUSPEND`, or :data:`RESOLVE`.
    For **suspend**, ``wait_for`` is the recipe's ``suspend_on`` events (what a
    hibernation wait should block until). For **resolve**, ``outcome`` is
    ``"landed"`` or ``"abandoned"``. ``directive`` is a one-line human framing.
    """

    kind: str
    directive: str
    wait_for: tuple[str, ...] = ()
    outcome: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "directive": self.directive,
            "wait_for": list(self.wait_for),
            "outcome": self.outcome,
        }


def normalize_signal(signal: str) -> str:
    return (signal or "").strip().lower()


def resolution_outcome(signal: str) -> str | None:
    """Map a terminal ``signal`` to a resolution outcome, or ``None`` if it is not
    terminal. Accepts an explicit ``resolved:landed`` / ``resolved:abandoned``
    form as well as the bare generic names."""
    s = normalize_signal(signal)
    if s.startswith("resolved:"):
        s = s.split(":", 1)[1]
    if s in _LANDED_SIGNALS:
        return LANDED
    if s in _ABANDONED_SIGNALS:
        return ABANDONED
    return None


def decide(recipe: Recipe, signal: str) -> DriverAction:
    """Map ``(recipe, signal)`` to the next :class:`DriverAction`.

    - A **terminal** signal (merged/landed/goal-met -> landed; abandoned/closed
      -> abandoned) yields **resolve**.
    - :data:`START`, or any of the recipe's ``suspend_on`` events (the world moved
      and there is something to react to), yields **work**.
    - :data:`WORK_DONE` / :data:`IDLE`, or an unrecognized non-terminal signal
      (be conservative -- don't busy-loop), yields **suspend** until a
      ``suspend_on`` event fires.
    """
    outcome = resolution_outcome(signal)
    if outcome is not None:
        verb = "Land it" if outcome == LANDED else "Unwind it"
        return DriverAction(
            kind=RESOLVE,
            directive=(
                f"{verb}: drive the worktree to a clean resolved state "
                f"(agent-dispatch resolve --outcome {outcome})."
            ),
            outcome=outcome,
        )

    s = normalize_signal(signal)
    suspend_on = tuple(recipe.suspend_on)
    if s == START or s in {e.lower() for e in suspend_on}:
        why = "start the loop" if s == START else f"the world moved ({s})"
        return DriverAction(
            kind=WORK,
            directive=f"Do a work pass -- {why}. Advance toward the resolution.",
            wait_for=suspend_on,
        )

    # WORK_DONE, IDLE, or anything unrecognized -> wait rather than spin.
    return DriverAction(
        kind=SUSPEND,
        directive=(
            "Nothing to do until the world moves. Hand the wait to the layer "
            "(agent-dispatch run --detach) until one of the suspend-on events fires."
        ),
        wait_for=suspend_on,
    )
