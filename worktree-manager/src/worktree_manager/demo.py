"""Aperture Labs demo fixture — mock worktrees for building/validating the Picker.

Because the Manager reaches the engine only across a process boundary
(``engine_client``), the Picker can be built, screenshotted, and demoed with a
**fake engine** that emits this fixture — no live ``agent-worktrees`` required.
The data is deliberately themed (Aperture Science / Cave Johnson) so a demo
screenshot is obviously synthetic and never leaks real machine/repo/session
particulars.

The rows match the engine's ``list --json --classify`` shape (contract v1), so
``engine_client._to_worktree`` parses them exactly as it parses the real engine.

The bulk of the roster below (the "management memo" titles: terse, absurd
tasks inflicted on the staff, exactly the tone of a Cave Johnson memo that
treats employees as expendable test subjects rather than people) is scraped
verbatim from ``docs/assets/worktree-picker.png`` -- the original Textual-
picker-era screenshot (``v1.0.0``, 2026-07-25) -- so the durable mock source
and the historical baseline stay the same voice rather than drifting apart.
The original 7-row roster (the "Portal quote" titles referencing GLaDOS, the
Companion Cube, the cake, etc.) predates that screenshot and is kept
unmodified for compatibility (see ``test_picker_app.py``'s "lemons"/"GLaDOS"
assertions). ``_MEMO_TITLES`` is deliberately separated from ``_ROWS`` so a
future generator (more volume than this fixed roster) has a clearly-labeled,
reusable bank of on-theme phrasing to draw from or extend, rather than
needing to reverse-engineer the tone from prose scattered through this file.
"""

from __future__ import annotations

import datetime as _dt

#: The project the demo welcome screen opens on.
DEMO_PROJECT = "copilot-extensions"

#: The demo machine (an Aperture facility, not a real host).
_MACHINE = "aperture-labs"

#: Cave-Johnson-memo-style task titles, scraped from the original v1.0.0
#: screenshot (``docs/assets/worktree-picker.png``, 2026-07-25) -- kept as a
#: standalone bank (not inlined only into ``_ROWS``) so later fixture/preview
#: work (or a future combinatorial generator) has a ready reference for the
#: "terse, absurd, employees-are-test-subjects" register, distinct from the
#: original 7 rows' longer "Portal quote pastiche" register. Grouped exactly
#: as the screenshot grouped them (by the section each row appeared in), not
#: because the mock data below must reuse that grouping verbatim.
_MEMO_TITLES: dict[str, tuple[str, ...]] = {
    "active": (
        "Ship the self-aware stapler",
        "Make the printer respect us",
        "Automate the screaming",
        "Promote the lab rats to management",
        "Bees. But for accounting.",
        "Weaponize the espresso machine",
        "Antigravity standing desk",
        "Combustion-powered morale",
        "Neural net for the vending machine",
    ),
    "recent": (
        "Teach the elevator regret",
        "Clone the good intern",
        "Turn the thermostat sentient",
    ),
    "completed": (
        "Sentient mop: phase two",
        "Reverse-engineer Mondays",
        "Small black hole, break room",
        "Sarcasm module for the help desk",
        "Weaponized optimism v2",
        "Give the roomba a promotion",
    ),
}


def _wt(idx: str, state: str, ahead: int, behind: int, dirty: bool,
        status: str, title: str, branch: str, **extra: object) -> dict:
    row = {
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
    row.update(extra)
    return row


def _ago(**delta: float) -> str:
    """An ISO timestamp ``delta`` in the past, from the real current time (not
    a frozen clock -- this fixture backs the live ``--demo``/preview CLI, not
    the frozen-clock golden tests in ``tests/production_picker``)."""
    return (_dt.datetime.now() - _dt.timedelta(**delta)).isoformat()


# Cave Johnson, we're done here — a synthetic roster of "test chambers".
# The original 7: the "Portal quote" register (unmodified; see module docstring).
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


def _memo_rows() -> list[dict]:
    """The scraped "management memo" rows, reconstructed with the SAME ids,
    ages, live/session indicators, follow-up markers, and PR states the
    original screenshot showed (the ``+`` prefix seen there is the follow-up
    glyph, not part of the title text). Built fresh each call so the ages
    stay relative to the real current time."""
    titles = _MEMO_TITLES
    rows = [
        # -- Active (the screenshot's live ●1/o/· SESS glyphs -> mux fields) --
        _wt("9578", "wip", 2, 0, False, "active", titles["active"][0],
            "feat/self-aware-stapler", started_at=_ago(minutes=27),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=6),
        _wt("cd0e", "wip", 1, 0, False, "active", titles["active"][1],
            "fix/printer-respect", started_at=_ago(hours=1),
            mux_attached=True, mux_clients=1, turn_count=4),
        _wt("4acd", "dirty", 0, 1, True, "active", titles["active"][2],
            "feat/automate-screaming", started_at=_ago(hours=5),
            mux_session=True, turn_count=9),
        _wt("4301", "wip", 4, 0, False, "active", titles["active"][3],
            "feat/lab-rats-to-management", started_at=_ago(hours=7),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=14,
            pr={"number": 55, "state": "open"}),
        _wt("afb7", "wip", 2, 0, False, "active", titles["active"][4],
            "feat/bees-for-accounting", started_at=_ago(hours=7),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=11,
            pr={"number": 44, "state": "open"}),
        _wt("1d41", "wip", 1, 0, False, "active", titles["active"][5],
            "feat/weaponized-espresso", started_at=_ago(hours=22),
            session_bound_live=True, follow_up=True, turn_count=21,
            pr={"number": 29, "state": "open"}),
        _wt("4cbe", "wip", 3, 0, False, "active", titles["active"][6],
            "feat/antigravity-standing-desk", started_at=_ago(days=2),
            session_bound_live=True, follow_up=True, turn_count=33,
            pr={"number": 95, "state": "open"}),
        _wt("7099", "wip", 2, 0, False, "active", titles["active"][7],
            "feat/combustion-morale", started_at=_ago(days=4),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=27,
            pr={"number": 83, "state": "open"}),
        _wt("0545", "wip", 1, 0, False, "active", titles["active"][8],
            "feat/vending-machine-neural-net", started_at=_ago(days=8),
            session_bound_live=True, follow_up=True, turn_count=52,
            pr={"number": 98, "state": "merged"}),
        # -- Recent (UNUSED / CONVO -- a held conversation, no commits) --
        _wt("48f7", "unused", 0, 0, False, "active", titles["recent"][0],
            "spike/elevator-regret", started_at=_ago(days=1), turn_count=0),
        _wt("3941", "unused", 0, 0, False, "active", titles["recent"][1],
            "spike/clone-the-intern", started_at=_ago(days=1), turn_count=3),
        _wt("b753", "unused", 0, 0, False, "active", titles["recent"][2],
            "spike/sentient-thermostat", started_at=_ago(days=4), turn_count=2),
        # -- Completed (finalized; PR state drives MERGED vs. plain done) --
        _wt("7ac4", "", 0, 0, False, "finalized", titles["completed"][0],
            "feat/sentient-mop-phase-two", completed_at=_ago(hours=1),
            pr={"number": 16, "state": "open"}),
        _wt("baa7", "", 0, 0, False, "finalized", titles["completed"][1],
            "chore/reverse-engineer-mondays", completed_at=_ago(days=2),
            pr={"number": 65, "state": "open"}),
        _wt("6b68", "", 0, 0, False, "finalized", titles["completed"][2],
            "fix/break-room-black-hole", completed_at=_ago(days=2)),
        _wt("2d3d", "", 0, 0, False, "finalized", titles["completed"][3],
            "feat/help-desk-sarcasm-module", completed_at=_ago(days=8),
            pr={"number": 90, "state": "merged"}),
        _wt("f7e5", "", 0, 0, False, "finalized", titles["completed"][4],
            "feat/weaponized-optimism-v2", completed_at=_ago(days=9),
            pr={"number": 49, "state": "open"}),
        _wt("1329", "", 0, 0, False, "finalized", titles["completed"][5],
            "feat/roomba-promotion", completed_at=_ago(days=13)),
    ]
    return rows


def aperture_worktrees() -> list[dict]:
    """The Aperture Labs worktree roster (engine ``list --json`` row shape)."""
    return [dict(r) for r in _ROWS] + _memo_rows()


def list_envelope() -> dict:
    """A full ``list --json`` envelope (version + worktrees) for the fake engine."""
    return {"version": 1, "worktrees": aperture_worktrees()}


def resolve_plan(worktree_id: str | None = None, *,
                 new: bool = False, bare_resume: bool = False) -> dict:
    """A harmless demo launch plan (the shape ``resolve --json`` emits).

    Obviously synthetic and side-effect-free: it "launches" a Python one-liner that
    just prints an Aperture Science line, so a demo of the Picker's launch/resume
    action exercises the whole resolve -> compose path without ever starting a real
    Copilot session. ``new`` invents a fresh test-chamber id.
    """
    wid = worktree_id or "aperture-labs-testchamber-new0"
    what = "creating + launching" if new else (
        "bare-resuming" if bare_resume else "resuming")
    return {
        "action": "exec",
        "work_dir": f"/aperture/testchambers/{wid[-4:]}",
        "status_path": f"/aperture/testchambers/{wid[-4:]}",
        "cmd": ["python", "-c",
                f"print('Aperture Labs: {what} {wid} -- for science.')"],
        "env": {"APERTURE_DEMO": "1"},
        "worktree_id": wid,
        "post_exit": True,
        "no_mux": True,
    }
