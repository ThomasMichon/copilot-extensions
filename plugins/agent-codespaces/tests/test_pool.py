"""Tests for the CodeSpace venue pool (inventory + budget + disposition)."""

from __future__ import annotations

import time

from agent_codespaces.lease import Lease
from agent_codespaces.lifecycle import CodespaceInfo
from agent_codespaces.pool import (
    CLEAN,
    DEFAULT_STALE_AFTER,
    FAILED,
    IDLE,
    IN_USE,
    PROVISIONING,
    STALE,
    build_pool,
    derive_disposition,
    is_running,
    machine_cores,
)
from agent_codespaces.status import STATE_PRUNABLE, STATE_RECOVERED


# --- machine_cores -------------------------------------------------------

def test_machine_cores_from_machine_tier():
    assert machine_cores("premiumLinux") == 8
    assert machine_cores("standardLinux32gb") == 4
    assert machine_cores("largePremiumLinux") == 16
    assert machine_cores("basicLinux32gb") == 2


def test_machine_cores_parses_embedded_core_count():
    assert machine_cores("custom16core") == 16


def test_machine_cores_unknown_is_zero():
    assert machine_cores("someWeirdMachine") == 0
    assert machine_cores("") == 0


# --- is_running ----------------------------------------------------------

def test_is_running_states():
    assert is_running("Available") is True
    assert is_running("Provisioning") is True   # transient pending == running
    assert is_running("Shutdown") is False      # stopped -> spends no cores
    assert is_running("Failed") is False        # terminal -> spends no cores


# --- derive_disposition precedence --------------------------------------

def _d(**kw):
    base = dict(
        state="Available", has_live_lease=False, has_beacon=False,
        marker=None, idle_age=None, stale_after=DEFAULT_STALE_AFTER,
    )
    base.update(kw)
    return derive_disposition(**base)


def test_disposition_failed_overrides_all():
    assert _d(state="Failed", has_live_lease=True) == FAILED


def test_disposition_live_lease_is_in_use():
    assert _d(has_live_lease=True) == IN_USE


def test_disposition_beacon_is_in_use_even_without_local_lease():
    assert _d(has_beacon=True) == IN_USE


def test_disposition_pending_is_provisioning_when_unheld():
    assert _d(state="Provisioning") == PROVISIONING
    # ...but a leased box still being provisioned is in-use by its holder.
    assert _d(state="Provisioning", has_live_lease=True) == IN_USE


def test_disposition_markers():
    assert _d(marker=STATE_PRUNABLE) == STALE
    assert _d(marker=STATE_RECOVERED) == CLEAN


def test_disposition_idle_ages_to_stale():
    assert _d(idle_age=10.0) == IDLE
    assert _d(idle_age=DEFAULT_STALE_AFTER + 1) == STALE


def test_disposition_default_is_idle():
    assert _d() == IDLE


# --- build_pool budget accounting ---------------------------------------

def _cs(name, state="Available", machine="premiumLinux", repo="o/r"):
    return CodespaceInfo(
        name=name, display_name=name, repository=repo, branch="main",
        state=state, machine=machine, account="", last_used_at="",
    )


def test_build_pool_budget_counts_only_running_cores():
    now = time.time()
    codespaces = [
        _cs("a", state="Available", machine="premiumLinux"),        # 8
        _cs("b", state="Shutdown", machine="largePremiumLinux"),    # 16, off budget
        _cs("c", state="Available", machine="standardLinux32gb"),   # 4
    ]
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=codespaces, leases=[], markers={},
    )
    assert budget.total_cores == 64
    assert budget.spent_cores == 12          # 8 + 4 (Shutdown b excluded)
    assert budget.headroom_cores == 52
    assert budget.running_count == 2
    assert budget.total_count == 3


def test_build_pool_unknown_cores_are_surfaced():
    # A machine tier neither in the map nor with a parseable core count.
    cs = CodespaceInfo(
        name="a", display_name="a", repository="o/r", branch="main",
        state="Available", machine="mysteryMachine", account="", last_used_at="",
    )
    _members, budget = build_pool(
        budget_cores=64, codespaces=[cs], leases=[], markers={},
    )
    assert budget.spent_cores == 0
    assert budget.unknown_cores_count == 1


def test_build_pool_derives_in_use_and_allocation_from_lease():
    now = time.time()
    lease = Lease(
        codespace="a", effort="my-effort", pid=123, host="dev6",
        acquired_at=now, heartbeat_at=now,
    )
    members, _budget = build_pool(
        now=now, codespaces=[_cs("a")], leases=[lease], markers={},
    )
    (m,) = members
    assert m.disposition == IN_USE
    assert m.holder_effort == "my-effort"
    assert m.holder_worktree is None
    assert m.holder_owner == "my-effort"
    assert m.holder_host == "dev6"
    d = m.to_dict()
    assert d["allocation"] == {
        "owner": "my-effort", "effort": "my-effort", "worktree": None,
        "host": "dev6", "beacon": None,
    }


def test_build_pool_surfaces_claim_owner_not_null():
    """A #897 claim (effort="", owner in worktree) must read as held by its
    worktree -- not a null allocation (dotfiles #904)."""
    now = time.time()
    wt = "/home/me/wt/type-filters-adoption-7qv"
    claim = Lease(
        codespace="a", effort="", pid=123, host="cloud1",
        acquired_at=now, heartbeat_at=now, worktree=wt,
    )
    members, _budget = build_pool(
        now=now, codespaces=[_cs("a")], leases=[claim], markers={},
    )
    (m,) = members
    assert m.disposition == IN_USE
    # effort is empty on a claim; the owner comes from the worktree.
    assert m.holder_effort is None
    assert m.holder_worktree == wt
    assert m.holder_owner == wt
    assert m.holder_host == "cloud1"
    d = m.to_dict()
    assert d["allocation"]["owner"] == wt
    assert d["allocation"]["effort"] is None
    assert d["allocation"]["worktree"] == wt
    # The key regression guard: a dispatched (claimed) box is NOT null-held.
    assert d["allocation"]["owner"] is not None


def test_build_pool_marks_prunable_as_stale_and_recovered_as_clean():
    codespaces = [_cs("p"), _cs("r", state="Shutdown")]
    members, _ = build_pool(
        codespaces=codespaces, leases=[],
        markers={"p": STATE_PRUNABLE, "r": STATE_RECOVERED},
    )
    by = {m.name: m.disposition for m in members}
    assert by["p"] == STALE
    assert by["r"] == CLEAN


def test_build_pool_ages_idle_box_to_stale_via_last_used():
    now = time.time()
    old = _cs("old")
    # last used 2 days ago, unheld -> stale
    old.last_used_at = _iso(now - 2 * 24 * 3600)
    fresh = _cs("fresh")
    fresh.last_used_at = _iso(now - 60)
    members, _ = build_pool(
        now=now, codespaces=[old, fresh], leases=[], markers={},
    )
    by = {m.name: m.disposition for m in members}
    assert by["old"] == STALE
    assert by["fresh"] == IDLE


# --- cross-machine L2 (Git-ref lease) overlay ----------------------------

def _l2(key, holder="m2/proj/wt-9#s", live=True, expires_at="2026-08-07T18:00:00Z"):
    from agent_codespaces.coordination import L2Lease
    return L2Lease(key=key, holder=holder, live=live, expires_at=expires_at)


def test_build_pool_l2_hold_marks_in_use_without_local_lease():
    """A live L2 lease held cross-machine (no local L1 lease) reads as in-use."""
    now = time.time()
    members, _ = build_pool(
        now=now, codespaces=[_cs("a")], leases=[], markers={},
        l2_leases={"a": _l2("a", holder="tmichon-cloud1/odsp-web/wt-abc#s1")},
    )
    (m,) = members
    assert m.disposition == IN_USE
    assert m.l2_live is True
    assert m.l2_holder == "tmichon-cloud1/odsp-web/wt-abc#s1"
    d = m.to_dict()
    assert d["l2"] == {
        "holder": "tmichon-cloud1/odsp-web/wt-abc#s1",
        "live": True,
        "expires_at": "2026-08-07T18:00:00Z",
    }


def test_build_pool_dead_l2_lease_does_not_force_in_use():
    """A released/expired (non-live) L2 lease is overlaid but not in-use."""
    now = time.time()
    members, _ = build_pool(
        now=now, codespaces=[_cs("a")], leases=[], markers={},
        l2_leases={"a": _l2("a", live=False)},
    )
    (m,) = members
    assert m.disposition == IDLE          # not forced in-use
    assert m.l2_live is False
    assert m.l2_holder == "m2/proj/wt-9#s"


def test_build_pool_no_l2_overlay_when_empty():
    """An empty overlay (``{}``) leaves the member exactly as pre-overlay."""
    members, _ = build_pool(
        codespaces=[_cs("a")], leases=[], markers={}, l2_leases={},
    )
    (m,) = members
    assert m.disposition == IDLE
    assert m.l2_live is False
    assert m.l2_holder is None
    assert m.to_dict()["l2"] == {"holder": None, "live": False, "expires_at": None}


def test_build_pool_l2_overlay_defaults_to_live_read(monkeypatch):
    """When ``l2_leases`` is omitted, build_pool reads via coordination
    (degrade-safe -- None collapses to no overlay)."""
    from agent_codespaces import coordination
    monkeypatch.setattr(
        coordination, "list_leases",
        lambda *a, **k: {"a": _l2("a", holder="tmichon-book2/odsp-web/wt-z#s")},
    )
    members, _ = build_pool(codespaces=[_cs("a")], leases=[], markers={})
    (m,) = members
    assert m.disposition == IN_USE
    assert m.l2_holder == "tmichon-book2/odsp-web/wt-z#s"


def test_build_pool_l2_read_failure_is_degrade_safe(monkeypatch):
    """A raising ``list_leases`` never breaks the pool -- overlay simply absent."""
    from agent_codespaces import coordination

    def boom(*a, **k):
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(coordination, "list_leases", boom)
    members, _ = build_pool(codespaces=[_cs("a")], leases=[], markers={})
    (m,) = members
    assert m.disposition == IDLE
    assert m.l2_holder is None


def test_build_pool_local_lease_takes_precedence_over_l2_owner():
    """When both an L1 lease and an L2 lease exist, the local allocation still
    names the L1 owner; the L2 block is an additive overlay."""
    now = time.time()
    lease = Lease(codespace="a", effort="mine", pid=1, host="dev6",
                  acquired_at=now, heartbeat_at=now)
    members, _ = build_pool(
        now=now, codespaces=[_cs("a")], leases=[lease], markers={},
        l2_leases={"a": _l2("a", holder="tmichon-dev6/odsp-web/wt-a#s")},
    )
    (m,) = members
    assert m.disposition == IN_USE
    assert m.holder_owner == "mine"       # L1 allocation unchanged
    assert m.l2_live is True              # L2 overlay still present


def test_picker_payload_l2_holder_rendered_when_no_local_lease():
    from agent_codespaces.pool import picker_payload
    now = time.time()
    members, budget = build_pool(
        now=now, codespaces=[_cs("a", repo="o/web-cs")], leases=[], markers={},
        l2_leases={"a": _l2("a", holder="tmichon-cloud1/odsp-web/wt-abc#s1")},
    )
    e = picker_payload(members, budget)["entries"][0]
    assert e["use"] == "in-use"
    assert e["holder"] == "wt-abc@tmichon-cloud1"
    assert "held cross-machine by wt-abc@tmichon-cloud1" in e["subtitle"]


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


# --- picker_payload (Worktree Picker CodeSpaces-pivot shape, D1) ----------

def test_picker_payload_shape_and_summary():
    import time as _t
    from agent_codespaces.pool import picker_payload

    now = _t.time()
    lease = Lease(codespace="held", effort="my-effort", pid=1, host="dev6",
                  acquired_at=now, heartbeat_at=now)
    codespaces = [
        _cs("held", state="Available", machine="premiumLinux", repo="o/web-cs"),   # 8, in-use
        _cs("free", state="Shutdown", machine="largePremiumLinux", repo="o/web-cs"),
    ]
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=codespaces, leases=[lease], markers={},
    )
    payload = picker_payload(members, budget, note="")

    assert set(payload) == {"entries", "summary"}
    by = {e["name"]: e for e in payload["entries"]}
    # in-use entry: holder rendered, health=running, use=in-use.
    assert by["held"]["disposition"] == IN_USE
    assert by["held"]["holder"] == "my-effort@dev6"
    assert by["held"]["health"] == "running"
    assert by["held"]["use"] == "in-use"
    assert by["held"]["repo"] == "web-cs"          # short repo (trailing segment)
    assert by["held"]["cores"] == "8"
    assert by["held"]["id"] == "held"              # id mirrors name for the pivot
    # free/stopped entry: no holder, health=stopped, use=free.
    assert by["free"]["holder"] == ""
    assert by["free"]["health"] == "stopped"
    assert by["free"]["use"] == "free"
    # summary carries the budget accounting + a (blank) note.
    s = payload["summary"]
    assert s["total_cores"] == 64
    assert s["spent_cores"] == 8                   # only the running box
    assert s["headroom_cores"] == 56
    assert s["note"] == ""


def test_picker_payload_unknown_cores_render_question_mark():
    from agent_codespaces.pool import picker_payload
    cs = CodespaceInfo(
        name="a", display_name="a", repository="o/r", branch="main",
        state="Available", machine="mysteryMachine", account="", last_used_at="",
    )
    members, budget = build_pool(budget_cores=64, codespaces=[cs], leases=[], markers={})
    payload = picker_payload(members, budget)
    assert payload["entries"][0]["cores"] == "?"
    assert payload["summary"]["note"] == ""        # default note is blank


def test_picker_payload_note_passthrough():
    from agent_codespaces.pool import picker_payload
    members, budget = build_pool(budget_cores=64, codespaces=[], leases=[], markers={})
    payload = picker_payload(members, budget, note="gh token is missing the 'codespace' scope")
    assert payload["entries"] == []
    assert "codespace" in payload["summary"]["note"]


def test_picker_payload_banner_sets_reserved_summary_keys():
    """#980: a non-empty ``banner`` rides the summary's reserved ``banner_text`` /
    ``banner_level`` keys (which the picker renders as a prominent alert), and is
    absent when no banner is supplied."""
    from agent_codespaces.pool import picker_payload
    members, budget = build_pool(budget_cores=64, codespaces=[], leases=[], markers={})
    # No banner -> no reserved keys.
    plain = picker_payload(members, budget)
    assert "banner_text" not in plain["summary"]
    assert "banner_level" not in plain["summary"]
    # With a banner -> reserved keys carry the text + (defaulted) level.
    msg = "gh token is missing the 'codespace' scope -- run: gh auth refresh"
    p = picker_payload(members, budget, banner=msg)
    assert p["summary"]["banner_text"] == msg
    assert p["summary"]["banner_level"] == "warn"
    p2 = picker_payload(members, budget, banner="boom", banner_level="error")
    assert p2["summary"]["banner_level"] == "error"


def test_picker_stream_frames_carry_banner():
    """The D2 stream envelope's ``summary`` frame carries the same banner as the
    one-shot payload (so a streamed/live pivot shows the scope alert too)."""
    from agent_codespaces.pool import picker_stream_frames
    members, budget = build_pool(budget_cores=64, codespaces=[], leases=[], markers={})
    frames = picker_stream_frames(members, budget, banner="scope missing")
    summ = [f for f in frames if f["type"] == "summary"]
    assert summ and summ[0]["summary"]["banner_text"] == "scope missing"


def test_orphaned_claim_flagged_when_worktree_path_gone(tmp_path):
    """3b: a #897 claim whose owner worktree PATH is gone reads as **orphaned** --
    ``occupancy`` becomes ``orphan`` (magenta in the pivot) while ``disposition``
    stays in-use (so Release still offers to free the stale lock), and the
    ``worktree`` column surfaces the claim's worktree dir id."""
    from agent_codespaces.pool import picker_payload
    now = time.time()
    gone = str(tmp_path / "worktrees" / "tmichon-cloud1-win-DEAD-9f3a")  # never created
    claim = Lease(codespace="held", effort="", pid=1, host="dev6",
                  acquired_at=now, heartbeat_at=now, worktree=gone)
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=[_cs("held", state="Available")],
        leases=[claim], markers={},
    )
    m = members[0]
    assert m.disposition == IN_USE       # a lease exists -> still in-use
    assert m.orphaned is True            # ...but its worktree is positively gone
    e = picker_payload(members, budget)["entries"][0]
    assert e["orphaned"] is True
    assert e["occupancy"] == "orphan"    # -> the magenta ORPHAN palette cell
    assert e["disposition"] == IN_USE    # unchanged: Release verb still gates on
    assert e["worktree"] == "tmichon-cloud1-win-DEAD-9f3a"  # which lock is stale


def test_live_claim_not_flagged_orphaned(tmp_path):
    """A claim whose owner worktree still exists on disk is NOT orphaned;
    ``occupancy`` mirrors the disposition and the worktree dir id surfaces."""
    from agent_codespaces.pool import picker_payload
    now = time.time()
    live = tmp_path / "worktrees" / "tmichon-cloud1-win-LIVE-1a2b"
    live.mkdir(parents=True)
    claim = Lease(codespace="held", effort="", pid=1, host="dev6",
                  acquired_at=now, heartbeat_at=now, worktree=str(live))
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=[_cs("held", state="Available")],
        leases=[claim], markers={},
    )
    assert members[0].orphaned is False
    e = picker_payload(members, budget)["entries"][0]
    assert e["orphaned"] is False
    assert e["occupancy"] == IN_USE      # mirrors disposition when not orphaned
    assert e["worktree"] == "tmichon-cloud1-win-LIVE-1a2b"


def test_advisory_borrow_never_orphaned():
    """An advisory borrow (effort owner, no worktree path) is never orphan-flagged
    -- ``_holder_worktree_gone`` only positively-kills an absolute-path owner."""
    from agent_codespaces.pool import picker_payload
    now = time.time()
    borrow = Lease(codespace="held", effort="my-effort", pid=1, host="dev6",
                   acquired_at=now, heartbeat_at=now)  # worktree="" (advisory)
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=[_cs("held", state="Available")],
        leases=[borrow], markers={},
    )
    assert members[0].orphaned is False
    e = picker_payload(members, budget)["entries"][0]
    assert e["occupancy"] == IN_USE
    assert e["worktree"] == "my-effort"  # advisory borrow surfaces via effort id


def test_picker_payload_friendly_name_and_subtitle():
    import time as _t
    from agent_codespaces.pool import picker_payload
    now = _t.time()
    lease = Lease(codespace="held", effort="my-effort", pid=1, host="dev6",
                  acquired_at=now, heartbeat_at=now)
    # A friendly display_name distinct from the durable name; and a free box whose
    # display_name equals its name (no redundant subtitle).
    held = CodespaceInfo(name="held", display_name="my-feature", repository="o/web-cs",
                         branch="main", state="Available", machine="premiumLinux",
                         account="", last_used_at="")
    free = CodespaceInfo(name="free", display_name="", repository="o/web-cs",
                         branch="main", state="Shutdown", machine="premiumLinux",
                         account="", last_used_at="")
    members, budget = build_pool(now=now, codespaces=[held, free], leases=[lease], markers={})
    by = {e["name"]: e for e in picker_payload(members, budget)["entries"]}
    # Durable id vs friendly name both present.
    assert by["held"]["name"] == "held"
    assert by["held"]["display"] == "my-feature"
    # Second metadata line carries the durable id + the claim.
    assert "held" in by["held"]["subtitle"]
    assert "claimed by my-effort on dev6" in by["held"]["subtitle"]
    # Free box: display falls back to name; no redundant id, no claim -> blank subtitle.
    assert by["free"]["display"] == "free"
    assert by["free"]["subtitle"] == ""


def test_picker_payload_group_status_worktree():
    import time as _t
    from agent_codespaces.pool import picker_payload
    now = _t.time()
    lease = Lease(codespace="held", effort="3bac", pid=1, host="dev6",
                  acquired_at=now, heartbeat_at=now)
    held = CodespaceInfo(name="held", display_name="my-feature",
                         repository="odsp-microsoft/odsp-web-codespaces", branch="main",
                         state="Available", machine="premiumLinux", account="acct1",
                         last_used_at="")
    members, budget = build_pool(now=now, codespaces=[held], leases=[lease], markers={})
    e = picker_payload(members, budget)["entries"][0]
    assert e["group"] == "odsp-web-codespaces @ acct1"
    assert e["status"] == "RUNNING"          # running box
    assert e["worktree"] == "3bac"           # claiming worktree short id (effort)


def test_picker_payload_status_stale_and_stopped():
    import time as _t
    from agent_codespaces.pool import picker_payload
    now = _t.time()
    stale = CodespaceInfo(name="s", display_name="", repository="o/r-codespaces",
                          branch="main", state="Shutdown", machine="premiumLinux",
                          account="a", last_used_at=_iso(now - 5 * 24 * 3600))
    stopped = CodespaceInfo(name="f", display_name="", repository="o/r-codespaces",
                            branch="main", state="Shutdown", machine="premiumLinux",
                            account="a", last_used_at=_iso(now - 60))
    members, budget = build_pool(now=now, codespaces=[stale, stopped], leases=[], markers={})
    by = {e["name"]: e for e in picker_payload(members, budget)["entries"]}
    assert by["s"]["status"] == "STALE"
    assert by["f"]["status"] == "STOPPED"
    assert by["s"]["worktree"] == ""         # unclaimed


# --- picker_stream_frames + diff_entries (D2 NDJSON streaming) -------------

def test_picker_stream_frames_envelope_order_and_rows():
    from agent_codespaces.pool import picker_payload, picker_stream_frames
    now = time.time()
    codespaces = [
        _cs("a", state="Available", machine="premiumLinux", repo="o/web-cs"),
        _cs("b", state="Shutdown", machine="premiumLinux", repo="o/web-cs"),
    ]
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=codespaces, leases=[], markers={})
    frames = picker_stream_frames(members, budget, note="")

    # begin -> row per CodeSpace -> summary -> done.
    assert frames[0] == {"type": "begin", "count": 2}
    assert [f["type"] for f in frames] == ["begin", "row", "row", "summary", "done"]
    assert frames[-1] == {"type": "done", "count": 2}
    # Streamed rows carry the identical entry shape as the one-shot payload.
    payload = picker_payload(members, budget, note="")
    assert [f["entry"] for f in frames if f["type"] == "row"] == payload["entries"]
    assert frames[3]["summary"] == payload["summary"]


def test_picker_stream_frames_empty_pool():
    from agent_codespaces.pool import picker_stream_frames
    members, budget = build_pool(budget_cores=64, codespaces=[], leases=[], markers={})
    frames = picker_stream_frames(members, budget)
    assert [f["type"] for f in frames] == ["begin", "summary", "done"]
    assert frames[0]["count"] == 0


def test_diff_entries_delta_and_removed():
    from agent_codespaces.pool import diff_entries
    prev = [{"id": "a", "status": "STOPPED"}, {"id": "b", "status": "RUNNING"}]
    curr = [{"id": "a", "status": "RUNNING"}, {"id": "c", "status": "RUNNING"}]
    deltas, removed = diff_entries(prev, curr)
    # 'a' changed and 'c' is new -> both are whole-row deltas; 'b' vanished.
    assert {e["id"] for e in deltas} == {"a", "c"}
    assert removed == ["b"]


def test_diff_entries_no_change_is_empty():
    from agent_codespaces.pool import diff_entries
    same = [{"id": "a", "status": "RUNNING"}]
    deltas, removed = diff_entries(same, [dict(same[0])])
    assert deltas == []
    assert removed == []
