"""Aperture Labs demo fixture — mock worktrees for building/validating the Picker.

Because the Manager reaches the engine only across a process boundary
(``engine_client``), the Picker can be built, screenshotted, and demoed with a
**fake engine** that emits this fixture — no live ``agent-worktrees`` required.
The data is deliberately themed (Aperture Science / Cave Johnson) so a demo
screenshot is obviously synthetic and never leaks real machine/repo/session
particulars.

The rows match the engine's ``list --json --classify`` shape (contract v1), so
``engine_client._to_worktree`` parses them exactly as it parses the real engine.
"""

from __future__ import annotations

#: The project the demo welcome screen opens on.
DEMO_PROJECT = "copilot-extensions"

#: The demo machine (an Aperture facility, not a real host).
_MACHINE = "aperture-labs"


def _wt(idx: str, state: str, ahead: int, behind: int, dirty: bool,
        status: str, title: str, branch: str) -> dict:
    return {
        "id": f"aperture-labs-testchamber-{idx}",
        "repo": DEMO_PROJECT,
        "machine": _MACHINE,
        "branch": branch,
        "title": title,
        "state": state,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
        "status": status,
        "path": f"/aperture/testchambers/{idx}",
    }


# Cave Johnson, we're done here — a synthetic roster of "test chambers".
_ROWS = [
    _wt("18c4", "wip", 3, 0, True, "active",
        "When life gives you lemons, DEMAND to see life's manager",
        "feat/combustible-lemons"),
    _wt("2a01", "dirty", 0, 2, True, "active",
        "Repulsion gel: do NOT drink the science juice",
        "fix/propulsion-gel-viscosity"),
    _wt("3b7e", "wip", 1, 0, False, "active",
        "GLaDOS boot sequence — still testing, for science",
        "feat/glados-genetic-lifeform"),
    _wt("4f22", "clean", 0, 0, False, "complete",
        "Weighted Companion Cube must be incinerated (regrettably)",
        "chore/companion-cube-incinerator"),
    _wt("59d0", "wip", 5, 1, True, "active",
        "The cake integration test is not a lie",
        "test/cake-is-not-a-lie"),
    _wt("6c8b", "clean", 0, 7, False, "complete",
        "Mantis-man program: mothballed per Legal",
        "spike/mantis-men"),
    _wt("7e15", "unused", 0, 0, False, "active",
        "Conversion gel pipeline (Cave signed off, mostly)",
        "feat/conversion-gel"),
]


def aperture_worktrees() -> list[dict]:
    """The Aperture Labs worktree roster (engine ``list --json`` row shape)."""
    return [dict(r) for r in _ROWS]


def list_envelope() -> dict:
    """A full ``list --json`` envelope (version + worktrees) for the fake engine."""
    return {"version": 1, "worktrees": aperture_worktrees()}
