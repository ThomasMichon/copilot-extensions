"""Example Labs demo fixture — mock worktrees for building/validating the Picker.

Because the Manager reaches the engine only across a process boundary
(``engine_client``), the Picker can be built, screenshotted, and demoed with a
**fake engine** that emits this fixture — no live ``agent-worktrees`` required.
The data is deliberately themed (Example Research / Cave Johnson) so a demo
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
The original 7-row roster (the "Portal quote" titles referencing Iris, the
Companion Cube, the cake, etc.) predates that screenshot and is kept
unmodified for compatibility (see ``test_picker_app.py``'s "lemons"/"Iris"
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
_MACHINE = "private-downstream-repo"

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
        "id": f"private-downstream-repo-testchamber-{idx}",
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
        "Iris boot sequence — still testing, for science",
        "feat/iris-genetic-lifeform"),
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
        # ``session_count`` values are illustrative (#3307 Phase 6's combined
        # SESS/TURNS column) -- roughly correlated with age/turn_count, never
        # exceeding it, so the demo render shows a believable session/turn
        # spread rather than every row reading the same ratio.
        #
        # ``claims_summary`` values below use ONLY kinds ``claims_cli`` can
        # actually produce today (pr, worktree, container, bridge, ssh, task
        # -- see ``claims_rank``'s own "kind-vocabulary gap" note: "bug"/
        # "issue"/"effort" are not yet real claimable kinds, so this fixture
        # doesn't fabricate them). Formatted to match ``claims_rank
        # .format_claim`` exactly (``"PR #55"``, bare ``"container <name>"``
        # for a kind with no ``#`` convention) -- note the real algorithm
        # always DROPS a PR's repo prefix in the short summary (the number
        # alone is enough given the row's own already-known repo context),
        # so a literally cross-repo *name* only ever surfaces in a
        # non-numeric claim kind (container/bridge/ssh/worktree), never in a
        # "PR #N"/"bug #N" label -- illustrated below via named container/
        # bridge/ssh/child-worktree claims rather than a fake "owner/repo#N"
        # PR label the real column would never actually show.
        #
        # ``live_intent``/``live_rest``/``live_intent_at`` demonstrate the
        # live-pulse detail line (``derive._pulse_level``): one of each
        # graded state (fresh/awaiting/stale) so a --demo render exercises
        # all three instead of every row falling back to the plain state
        # label.
        #
        # ``last_resumed_at`` on a couple of otherwise-old rows demonstrates
        # the new USED column diverging from AGE -- a worktree can be old
        # (AGE) yet just touched (USED), which is the whole point of the
        # column (and already what the Recent section sorts by, Phase 3).
        _wt("9578", "wip", 2, 0, False, "active", titles["active"][0],
            "feat/self-aware-stapler", started_at=_ago(minutes=27),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=6,
            session_count=1, live_intent="Running the retry-budget test suite",
            live_rest="busy", live_intent_at=_ago(minutes=2)),
        _wt("cd0e", "wip", 1, 0, False, "active", titles["active"][1],
            "fix/printer-respect", started_at=_ago(hours=1),
            mux_attached=True, mux_clients=1, turn_count=4, session_count=1,
            claims_summary="task 41f2",
            # No live-pulse intent here -- demonstrates the second line's
            # OTHER activity source: the disposition-asserted `activity`
            # field (`agent-worktrees status --activity`), not a fallback to
            # bare STATE (which the second line no longer ever shows).
            activity="Reprinting the apology memo in Comic Sans"),
        _wt("4acd", "dirty", 0, 1, True, "active", titles["active"][2],
            "feat/automate-screaming", started_at=_ago(hours=5),
            mux_session=True, turn_count=9, session_count=2,
            claims_summary="container agent-containers-lab3"),
        _wt("4301", "wip", 4, 0, False, "active", titles["active"][3],
            "feat/lab-rats-to-management", started_at=_ago(hours=7),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=14,
            pr={"number": 55, "state": "open"}, session_count=2,
            claims_summary="PR #55",
            live_intent="Waiting on your answer about the deploy window",
            live_rest="awaiting-operator"),
        _wt("afb7", "wip", 2, 0, False, "active", titles["active"][4],
            "feat/bees-for-accounting", started_at=_ago(hours=7),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=11,
            pr={"number": 44, "state": "open"}, session_count=1,
            claims_summary="PR #44"),
        _wt("1d41", "wip", 1, 0, False, "active", titles["active"][5],
            "feat/weaponized-espresso", started_at=_ago(hours=22),
            session_bound_live=True, follow_up=True, turn_count=21,
            pr={"number": 29, "state": "open"}, session_count=3,
            claims_summary="PR #29",
            live_intent="Investigating the flaky espresso sensor logs",
            live_rest="idle", live_intent_at=_ago(hours=2)),
        _wt("4cbe", "wip", 3, 0, False, "active", titles["active"][6],
            "feat/antigravity-standing-desk", started_at=_ago(days=2),
            session_bound_live=True, follow_up=True, turn_count=33,
            pr={"number": 95, "state": "open"}, session_count=4,
            claims_summary="PR #95 \u00b7 container agent-containers-standing-desk"),
        _wt("7099", "wip", 2, 0, False, "active", titles["active"][7],
            "feat/combustion-morale", started_at=_ago(days=4),
            mux_attached=True, mux_clients=1, follow_up=True, turn_count=27,
            pr={"number": 83, "state": "open"}, session_count=3,
            claims_summary="PR #83", last_resumed_at=_ago(minutes=45),
            # #3307 follow-up: claims_links demonstrates the real terminal
            # hyperlink the CLAIMS column now supports -- the label matches
            # claims_summary above; a --demo/--preview render exercises the
            # real production_picker code path, so this shows the link
            # style actually applied, not just the plain string.
            claims_links=[{
                "label": "PR #83",
                "url": "https://github.com/private-downstream-repo/testchambers/pull/83",
            }]),
        _wt("0545", "wip", 1, 0, False, "active", titles["active"][8],
            "feat/vending-machine-neural-net", started_at=_ago(days=8),
            session_bound_live=True, follow_up=True, turn_count=52,
            pr={"number": 98, "state": "merged"}, session_count=5,
            # Merged PR claims naturally roll off claims_summary (the real
            # engine's summarize_claims filters non-live states) -- left
            # unset here to match, even though the row still shows the
            # merged PR badge elsewhere.
            last_resumed_at=_ago(hours=3)),
        # -- Recent (UNUSED / CONVO -- a held conversation, no commits) --
        _wt("48f7", "unused", 0, 0, False, "active", titles["recent"][0],
            "spike/elevator-regret", started_at=_ago(days=1), turn_count=0,
            session_count=1),
        _wt("3941", "unused", 0, 0, False, "active", titles["recent"][1],
            "spike/clone-the-intern", started_at=_ago(days=1), turn_count=3,
            session_count=1, claims_summary="worktree spike-3941-child"),
        _wt("b753", "unused", 0, 0, False, "active", titles["recent"][2],
            "spike/sentient-thermostat", started_at=_ago(days=4), turn_count=2,
            session_count=2, claims_summary="bridge session-9f21",
            last_resumed_at=_ago(hours=1)),
        # -- Completed (finalized; PR state drives MERGED vs. plain done) --
        _wt("7ac4", "", 0, 0, False, "finalized", titles["completed"][0],
            "feat/sentient-mop-phase-two", completed_at=_ago(hours=1),
            pr={"number": 16, "state": "open"}, claims_summary="PR #16"),
        _wt("baa7", "", 0, 0, False, "finalized", titles["completed"][1],
            "chore/reverse-engineer-mondays", completed_at=_ago(days=2),
            pr={"number": 65, "state": "open"}, claims_summary="PR #65"),
        _wt("6b68", "", 0, 0, False, "finalized", titles["completed"][2],
            "fix/break-room-black-hole", completed_at=_ago(days=2),
            claims_summary="ssh private-downstream-repo-bench2"),
        _wt("2d3d", "", 0, 0, False, "finalized", titles["completed"][3],
            "feat/help-desk-sarcasm-module", completed_at=_ago(days=8),
            pr={"number": 90, "state": "merged"}),
        _wt("f7e5", "", 0, 0, False, "finalized", titles["completed"][4],
            "feat/weaponized-optimism-v2", completed_at=_ago(days=9),
            pr={"number": 49, "state": "open"}, claims_summary="PR #49"),
        _wt("1329", "", 0, 0, False, "finalized", titles["completed"][5],
            "feat/roomba-promotion", completed_at=_ago(days=13)),
    ]
    return rows


def aperture_worktrees() -> list[dict]:
    """The Example Labs worktree roster (engine ``list --json`` row shape)."""
    return [dict(r) for r in _ROWS] + _memo_rows()


def list_envelope() -> dict:
    """A full ``list --json`` envelope (version + worktrees) for the fake engine."""
    return {"version": 1, "worktrees": aperture_worktrees()}


def resolve_plan(worktree_id: str | None = None, *,
                 new: bool = False, bare_resume: bool = False) -> dict:
    """A harmless demo launch plan (the shape ``resolve --json`` emits).

    Obviously synthetic and side-effect-free: it "launches" a Python one-liner that
    just prints an Example Research line, so a demo of the Picker's launch/resume
    action exercises the whole resolve -> compose path without ever starting a real
    Copilot session. ``new`` invents a fresh test-chamber id.
    """
    wid = worktree_id or "private-downstream-repo-testchamber-new0"
    what = "creating + launching" if new else (
        "bare-resuming" if bare_resume else "resuming")
    return {
        "action": "exec",
        "work_dir": f"/aperture/testchambers/{wid[-4:]}",
        "status_path": f"/aperture/testchambers/{wid[-4:]}",
        "cmd": ["python", "-c",
                f"print('Example Labs: {what} {wid} -- for science.')"],
        "env": {"APERTURE_DEMO": "1"},
        "worktree_id": wid,
        "post_exit": True,
        "no_mux": True,
    }
