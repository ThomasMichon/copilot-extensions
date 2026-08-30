"""Identity-obscuring for shareable picker captures.

Turn real worktree dumps (from ``<project> list --json``) into a **synthetic
picker data source** with every identifying particular scrubbed -- real machine
names -> stable codenames, repo/branch -> generic, titles -> a demo pool, PR
url/number/sha and paths/branches/summaries removed -- while preserving the
*shape* (states, sync tags, ages, sessions, dispositions) that makes a capture
look authentic. Feed the result to :func:`agent_worktrees.picker_tui.capture`.

Realizes the picker vision's obscured / shareable capture
(Features/auditable-testable-rendering): a faithful, safe-to-publish image from
real fleet data, with no private names leaked.
"""
from __future__ import annotations

import datetime
import hashlib
import random
import types
from typing import Any, Iterable, Optional

from . import derive

# Stable, generic machine codenames (assigned in first-seen order).
DEMO_MACHINES = ["Nova", "Orbit", "Atlas", "Vega", "Lyra", "Sol", "Echo", "Iris"]

# Whimsical "eccentric R&D lab" placeholder titles -- ORIGINAL writing, no
# trademarks and no verbatim quotes, safe for a public README. The over-the-top
# mad-science-founder *spirit*, not any specific franchise.
DEMO_TITLES = [
    "Weaponize the espresso machine", "Teach the elevator regret",
    "Ship the self-aware stapler", "Bottle lightning (Q3 stretch)",
    "Replace sprinklers with lasers", "Neural net for the vending machine",
    "Small black hole, break room", "Make the printer respect us",
    "Portable sun, pocket-sized", "Sentient mop: phase two",
    "Automate the screaming", "Give the roomba a promotion",
    "Frictionless handshake initiative", "Reverse-engineer Mondays",
    "Teach gravity a lesson", "Monetize the office ghost",
    "Emotional support drone fleet", "Combustion-powered morale",
    "Fireproof the enthusiasm", "Weaponized optimism v2",
    "Distill pure productivity", "Promote the lab rats to management",
    "Teleport the mailroom", "Bees. But for accounting.",
    "Parking lot: bigger on the inside", "Clone the good intern",
    "Antigravity standing desk", "Teach the toaster diplomacy",
    "Sarcasm module for the help desk", "Cross-breed ferns with the WiFi",
    "Turn the thermostat sentient", "Grow a second Tuesday",
    "Domesticate the server rack", "Insure the office against gravity",
]

# Keep a readable hero: cap each machine/env, preferring the interesting states.
_STATE_PRI = {"ACTIVE": 0, "WIP": 1, "DIRTY": 1, "CONVO": 2, "ORPHAN": 2,
              "UNUSED": 3, "FINAL": 5, "GONE": 6}


def _scrub(w: dict, disp: str, env: str) -> dict:
    """Strip identifying particulars from one raw worktree dict (title is set
    later, uniquely; here it is blanked so no real title ever enters a record)."""
    wid = w.get("id", "") or ""
    id4 = wid[-4:] if len(wid) >= 4 else (wid or "0000")
    ob = dict(w)
    ob["id"] = f"{disp.lower()}-{env.lower()}-{id4}"   # norm reads id[-4:]
    ob["machine"] = disp
    ob["title"] = ""
    ob["summary"] = ""        # disposition summary would ride into the title
    ob["live_intent"] = ""    # activity-pulse text
    ob.pop("path", None)
    ob.pop("branch", None)
    pr = w.get("pr")
    if isinstance(pr, dict) and pr:
        num = (int(hashlib.sha1((wid + "pr").encode()).hexdigest(), 16) % 89) + 11
        ob["pr"] = {"state": pr.get("state"), "number": num}
    if isinstance(w.get("prs"), list) and w["prs"]:
        ob["prs"] = [{"state": (p or {}).get("state")} for p in w["prs"]]
    return ob


def obscured_source(
    machine_dumps: Iterable[tuple[str, str, bool, list]],
    *,
    repo: str = "my-project",
    branch: str = "main",
    titles: Optional[list[str]] = None,
    machine_names: Optional[list[str]] = None,
    per_source_cap: Optional[int] = None,
    seed: int = 1,
    now: Optional[datetime.datetime] = None,
) -> Any:
    """Build a scrubbed, render-ready picker source from real worktree dumps.

    ``machine_dumps`` -- an iterable of ``(machine, env, is_local, worktrees)``
    where ``worktrees`` is the raw ``list --json`` worktree list for that
    machine/environment. Real ``machine`` names map to stable codenames; every
    machine/env is marked *ready* so an *All machines* capture aggregates them.

    ``per_source_cap`` caps each machine/env to its most interesting states
    (keeps a hero readable). Titles are assigned **uniquely** across the kept set
    from ``titles`` (default :data:`DEMO_TITLES`). Deterministic given ``seed``.
    """
    pool = list(titles or DEMO_TITLES)
    random.Random(seed).shuffle(pool)
    codes = list(machine_names or DEMO_MACHINES)
    real_to_code: dict[str, str] = {}

    def code(real: str) -> str:
        real_to_code.setdefault(real, codes[len(real_to_code) % len(codes)])
        return real_to_code[real]

    derive.NOW = now or datetime.datetime.now()
    desc: list = []
    local: Optional[tuple[str, str]] = None
    kept: list[list[dict]] = []
    for real, env, is_local, wts in machine_dumps:
        disp = code(real)
        desc.append((f"{disp} {env}", disp, env, True))
        if is_local and local is None:
            local = (disp, env)
        normed = [derive.norm(_scrub(w, disp, env), disp, env) for w in (wts or [])]
        normed.sort(key=lambda r: (_STATE_PRI.get(r["state"], 4), r["age_secs"]))
        if per_source_cap:
            normed = normed[:per_source_cap]
        kept.append(normed)

    records: list[dict] = []
    ti = 0
    for normed in kept:
        for r in normed:
            title = pool[ti % len(pool)]
            ti += 1
            r["title"] = ("\u271a " if r["follow_up"] else "") + title
            if isinstance(r.get("raw"), dict):
                r["raw"]["title"] = title
            records.append(r)

    if local is None and desc:
        local = (desc[0][1], desc[0][2])

    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = f"{local[0]} \u00b7 {local[1].lower()}"
    src.REPO = repo
    src.BRANCH = branch
    src.machines = lambda: desc
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.for_source = derive.for_source
    src.load = lambda: records
    return src


def settle_seconds(machine_dumps: Iterable[tuple[str, str, bool, list]]) -> float:
    """A safe ``settle`` for :func:`capture_async` given the dumps: enough for the
    ``live=False`` engine to stagger every remote machine/env to *ready*."""
    remotes = sum(1 for _m, _e, is_local, _w in machine_dumps if not is_local)
    return 1.4 + 1.1 * max(0, remotes) + 1.0
