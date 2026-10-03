from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock

import agent_dispatch
from agent_dispatch import board_cli, worktree_status_relay


def test_length_display_formats_and_degrades_gracefully():
    """`_length_display` -- the Tasks board LENGTH column's own unit test,
    independent of the `_build()`/relay plumbing above."""
    fresh = {
        "confirmed": True, "observed_at": 1000.0,
        "value": {"session_count": 3, "turn_count": 25},
    }
    assert worktree_status_relay._length_display(fresh, stale=False) == "3s 25t"
    assert worktree_status_relay._length_display(fresh, stale=True) is None
    assert worktree_status_relay._length_display(None, stale=False) is None
    unconfirmed = {**fresh, "confirmed": False}
    assert worktree_status_relay._length_display(unconfirmed, stale=False) is None
    malformed = {"confirmed": True, "observed_at": 1000.0, "value": {"turn_count": 25}}
    assert worktree_status_relay._length_display(malformed, stale=False) is None
    # A never-resumed worktree's session_turns is None -- not fabricated as 0.
    no_turns_yet = {
        "confirmed": True, "observed_at": 1000.0,
        "value": {"session_count": 0, "turn_count": None},
    }
    assert worktree_status_relay._length_display(no_turns_yet, stale=False) is None


def test_build_groups_and_expires_activity(monkeypatch):
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {
                "id": "active",
                "status": "started",
                "activity": "ACTIVE",
                "activity_updated_at": 950.0,
                "updated_at": 10,
                "repo": "github.com/example/repo",
            },
            {
                "id": "stale",
                "status": "started",
                "activity": "ACTIVE",
                "activity_updated_at": 800.0,
                "updated_at": 9,
                "repo": "github.com/example/repo",
            },
            {
                "id": "blocked",
                "status": "queued",
                "awaiting_steer": True,
                "updated_at": 11,
            },
            {
                "id": "dormant",
                "status": "suspended",
                "updated_at": 8,
            },
        ],
        machine="m1",
        recent_mins=120,
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["active"]["group"] == "Started"
    assert by_id["active"]["activity"] == "ACTIVE"
    assert by_id["active"]["repo_name"] == "repo"
    assert by_id["stale"]["activity"] is None
    assert by_id["blocked"]["group"] == "Blocked"
    assert by_id["dormant"]["group"] == "Suspended"


def test_build_orders_started_ahead_of_queued(monkeypatch):
    """Operator feedback 2026-09-20 (item 1): Started is more interesting to
    inspect at a glance than Queued (a task not yet running). This asserts
    `board_cli._build`'s own `GROUPS`-driven sort directly -- `__main__.py`'s
    byte-identical `_BOARD_GROUPS`/`_board_sort_key` has its own coverage in
    `test_cli.py::test_sort_orders_by_group_priority`."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {"id": "submitted", "status": "submitted", "updated_at": 100},
            {"id": "completed", "status": "completed", "updated_at": 100},
            {"id": "blocked", "status": "started", "awaiting_steer": True,
             "updated_at": 100},
            {"id": "queued", "status": "queued", "updated_at": 100},
            {"id": "proposed", "status": "proposed", "updated_at": 100},
            {"id": "abandoned", "status": "abandoned", "updated_at": 100},
            {"id": "started", "status": "started", "updated_at": 100},
            {"id": "suspended", "status": "suspended", "updated_at": 100},
        ],
        machine="m1",
        recent_mins=120,
    )
    assert [row["group"] for row in rows] == [
        "Blocked", "Proposed", "Started", "Queued", "Suspended",
        "Submitted", "Completed", "Abandoned",
    ]


def test_build_wt_live_reflects_headless_activity_only(monkeypatch):
    """Phase 3: `wt_live` reuses the already-computed `activity`/
    `activity_updated_at` (a headless self-report) -- it is blank, not a
    confirmed "not live", for a CLI-embodied task with no such signal."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {"id": "active", "status": "started", "activity": "ACTIVE",
             "activity_updated_at": 990.0},
            {"id": "stalled", "status": "started", "activity": "STALLED",
             "activity_updated_at": 940.0},
            {"id": "cli-embodied", "status": "started"},  # no activity ever set
        ],
        machine="m1",
        recent_mins=120,
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["active"]["wt_live"] == "active"
    assert by_id["stalled"]["wt_live"] == "stalled 1m"
    assert by_id["cli-embodied"]["wt_live"] is None


def test_activity_phrase_prioritizes_wt_live_then_hold_then_lifecycle():
    """`_activity_phrase` is the Tasks pane's standardized subtitle-line
    activity text (2026-09-29 row-shape standardization): a real headless
    liveness signal always wins; otherwise a hold, then awaiting-steer, then
    the raw lifecycle status supply a sensible fallback phrase."""
    assert board_cli._activity_phrase({"status": "started"}, "active") == "active"
    assert board_cli._activity_phrase(
        {"status": "started", "hold_reason": "operator pause"}, "active"
    ) == "active"  # wt_live still wins even while held
    assert board_cli._activity_phrase(
        {"status": "started", "hold_reason": "operator pause"}, None
    ) == "paused — operator pause"
    long_reason = "x" * 60
    truncated = board_cli._activity_phrase(
        {"status": "started", "hold_reason": long_reason}, None
    )
    assert truncated.startswith("paused — ")
    assert len(truncated) < len("paused — " + long_reason)
    assert truncated.endswith("…")
    assert board_cli._activity_phrase(
        {"status": "started", "awaiting_steer": True}, None
    ) == "awaiting your steer"
    assert board_cli._activity_phrase({"status": "suspended"}, None) == (
        "suspended — no live session"
    )
    assert board_cli._activity_phrase({"status": "queued"}, None) == "queued"
    assert board_cli._activity_phrase(
        {"status": "queued", "pool": {"kind": "headless"}}, None
    ) == "queued for a worker"
    assert board_cli._activity_phrase({"status": "proposed"}, None) == (
        "awaiting approval"
    )
    assert board_cli._activity_phrase({"status": "claimed"}, None) == (
        "claimed, starting…"
    )
    assert board_cli._activity_phrase({"status": "started"}, None) == "in progress"
    assert board_cli._activity_phrase({"status": "completed"}, None) == "completed"
    assert board_cli._activity_phrase({"status": "confirmed"}, None) == "confirmed"
    assert board_cli._activity_phrase({"status": "abandoned"}, None) == "abandoned"


def test_embodiment_tag_only_marks_the_non_default_cli_interface():
    """`_embodiment_tag` mirrors Worktrees' `[system]`/`[delegate]`/`[acp]`
    title-prefix convention: only the NON-default interface (CLI, for a
    Task) is tagged. A confirmed headless liveness signal always wins over
    the heuristic, and a not-yet-embodied task is never tagged."""
    # Confirmed headless (a real wt_live signal) -> no tag, regardless of
    # owner_session_id.
    assert board_cli._embodiment_tag(
        {"status": "started", "owner_session_id": "s1"}, "active"
    ) is None
    # Owned + live, but NO headless signal -> heuristically CLI.
    assert board_cli._embodiment_tag(
        {"status": "started", "owner_session_id": "s1"}, None
    ) == "cli"
    assert board_cli._embodiment_tag(
        {"status": "claimed", "owner_session_id": "s1"}, None
    ) == "cli"
    # No owner session yet -> nothing to tag.
    assert board_cli._embodiment_tag({"status": "started"}, None) is None
    # Not embodied at all -> nothing to tag.
    assert board_cli._embodiment_tag(
        {"status": "queued", "owner_session_id": "s1"}, None
    ) is None


def test_subtitle_for_task_assembles_tag_repo_title_and_phrase():
    """`_subtitle_for_task` composes the Tasks pane's standardized second
    line: ``[tag] <repo> <title> - <phrase>`` (tag/repo optional)."""
    cli_task = {
        "id": "t-1", "title": "Fix the thing", "status": "started",
        "owner_session_id": "s1", "repo_name": "example-repo",
    }
    assert board_cli._subtitle_for_task(cli_task, wt_live=None) == (
        "[cli] example-repo Fix the thing - in progress"
    )
    headless_task = {
        "id": "t-2", "title": "Nightly sweep", "status": "started",
        "owner_session_id": "s2", "repo_name": "example-repo",
    }
    assert board_cli._subtitle_for_task(headless_task, wt_live="active") == (
        "example-repo Nightly sweep - active"
    )
    no_repo_task = {"id": "t-3", "title": "Bare task", "status": "queued"}
    assert board_cli._subtitle_for_task(no_repo_task, wt_live=None) == (
        "Bare task - queued"
    )
    untitled_task = {"id": "t-4", "status": "proposed"}
    assert board_cli._subtitle_for_task(untitled_task, wt_live=None) == (
        "t-4 - awaiting approval"
    )


def test_build_populates_subtitle_and_drops_title_repo_from_columns(monkeypatch):
    """2026-09-29 row-shape standardization: line 1 (`columns`) stays pure
    stats; the title/repo/activity phrase move to line 2 (`subtitle`), and
    the manifest no longer declares a `title`/`repo_name` column."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {
                "id": "t-1", "title": "Fix the thing", "status": "started",
                "owner_session_id": "s1", "repo": "github.com/example/repo",
                "activity": "ACTIVE", "activity_updated_at": 990.0,
                "updated_at": 5,
            },
        ],
        machine="m1",
        recent_mins=120,
    )
    row = rows[0]
    assert row["subtitle"] == "repo Fix the thing - active"
    assert "title" in row  # still present on the row (for `{title}` action templating)
    assert "repo_name" in row  # still present (used to compose the subtitle)


def test_tasks_pivot_manifest_exposes_submitted_complete_and_abandon_actions():
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "pivots"
            / "agent-dispatch.json"
        ).read_text(encoding="utf-8")
    )
    actions = {action["key"]: action for action in manifest["actions"]}

    assert actions["complete-submitted"]["run"][:2] == ["agent-dispatch", "confirm"]
    assert actions["complete-submitted"]["when"] == {
        "group": "Submitted",
        "require_verification": "True",
    }
    assert "Submitted" in actions["abandon"]["when"]["group"]

    manifest = json.loads(
        (Path(__file__).parents[1] / "pivots" / "agent-dispatch.json")
        .read_text(encoding="utf-8")
    )
    column_keys = [c["key"] for c in manifest["columns"]]
    assert "title" not in column_keys
    assert "repo_name" not in column_keys
    assert manifest["entry"]["subtitle"] == "subtitle"


def test_build_cli_openable_matches_interactive_embody_statuses(monkeypatch):
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {"id": "proposed", "status": "proposed", "updated_at": 10},
            {"id": "queued", "status": "queued", "updated_at": 9},
            {"id": "suspended", "status": "suspended", "updated_at": 8},
            {"id": "blocked", "status": "suspended", "awaiting_steer": True, "updated_at": 7},
            {"id": "pooled", "status": "queued", "pool": {"kind": "headless"}, "updated_at": 6},
            {"id": "held", "status": "queued", "hold_reason": "pause", "updated_at": 5},
            {"id": "claimed", "status": "claimed", "updated_at": 4},
            {"id": "started", "status": "started", "updated_at": 3},
            {"id": "submitted", "status": "submitted", "updated_at": 2},
        ],
        machine="m1",
        recent_mins=120,
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["proposed"]["cli_openable"] is True
    assert by_id["queued"]["cli_openable"] is True
    assert by_id["suspended"]["cli_openable"] is True
    assert by_id["blocked"]["cli_openable"] is False
    assert by_id["pooled"]["cli_openable"] is False
    assert by_id["held"]["cli_openable"] is False
    assert by_id["claimed"]["cli_openable"] is False
    assert by_id["started"]["cli_openable"] is False
    assert by_id["submitted"]["cli_openable"] is False


def test_build_group_paused_for_held_task(monkeypatch):
    """Phase 7 follow-up (2026-09-29): a durable operator-set hold reads as
    its own ``Paused`` group -- never conflated with system-``Suspended`` or
    with ``Blocked``/awaiting-steer -- but a terminal status still wins."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {"id": "paused-started", "status": "started",
             "hold_reason": "operator pause", "updated_at": 10},
            {"id": "paused-blocked", "status": "suspended",
             "awaiting_steer": True, "hold_reason": "operator pause",
             "updated_at": 9},
            {"id": "paused-abandoned", "status": "abandoned",
             "hold_reason": "operator pause", "updated_at": 8},
        ],
        machine="m1",
        recent_mins=120,
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["paused-started"]["group"] == "Paused"
    assert by_id["paused-started"]["held"] is True
    assert by_id["paused-blocked"]["group"] == "Paused"
    assert by_id["paused-abandoned"]["group"] == "Abandoned"


def test_build_charter_is_always_populated(monkeypatch):
    """Phase 7's charter card gap: every row now carries a real
    ``charter.*`` payload (title/status/link/body) instead of a hard-`False`
    `has_charter` -- structured metadata plus the raw prompt verbatim."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {
                "id": "t-1",
                "title": "Fix the thing",
                "status": "queued",
                "repo": "github.com/example/repo",
                "source": "registrar:nightly",
                "origin_ref": "recipe:cleanup",
                "target_machine": "m1",
                "labels": ["urgent", "bugfix"],
                "goal": "Make the thing work again.",
                "done_criteria": "Tests pass.",
                "prompt": "Please fix the thing.",
                "updated_at": 5,
            },
            {
                "id": "t-2",
                "title": "Bare task",
                "status": "proposed",
                "prompt": "Just do it.",
                "updated_at": 4,
            },
        ],
        machine="m1",
        recent_mins=120,
    )
    by_id = {row["id"]: row for row in rows}
    full = by_id["t-1"]["charter"]
    assert by_id["t-1"]["has_charter"] is True
    assert full["title"] == "Fix the thing"
    assert full["status"] == "queued"
    assert "`repo`" in full["body"]
    assert "registrar:nightly" in full["body"]
    assert "recipe:cleanup" in full["body"]
    assert "urgent, bugfix" in full["body"]
    assert "Make the thing work again." in full["body"]
    assert "Tests pass." in full["body"]
    assert "Please fix the thing." in full["body"]

    bare = by_id["t-2"]["charter"]
    assert by_id["t-2"]["has_charter"] is True
    assert "no durable goal recorded" in bare["body"]
    assert "_not specified_" in bare["body"]
    assert "Just do it." in bare["body"]


def test_build_artifacts_summary_reads_claims_from_relay(monkeypatch):
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [{
            "id": "t1",
            "status": "started",
            "repo": "github.com/example/repo",
            "owner": "m1/wt1",
            "target_worktree": "wt1",
        }],
        machine="m1",
        recent_mins=120,
        relay_fetch_many=lambda refs: {
            ("github.com/example/repo", "wt1"): {
                "repo": "github.com/example/repo",
                "worktree_id": "wt1",
                "fetched_at": 995.0,
                "poll_interval_seconds": 10.0,
                "bundle": {
                    "facts": {
                        "claims": {
                            "confirmed": True,
                            "observed_at": 995.0,
                            "value": {"resources": [{"kind": "pr"}], "owner_ref": "abc/def"},
                        },
                        "session_length": {
                            "confirmed": True,
                            "observed_at": 995.0,
                            "value": {"session_count": 3, "turn_count": 25},
                        },
                    }
                },
            }
        },
    )
    assert rows[0]["artifacts_summary"] == "1 claim, owner ref"
    assert rows[0]["worktree_status"]["status"] == "fresh"
    assert rows[0]["length_display"] == "3s 25t"


def test_build_length_display_blank_when_no_worktree_or_stale(monkeypatch):
    """`length_display` (the Tasks board's LENGTH column) is blank -- never a
    fabricated `0s 0t` -- when there is no claiming worktree at all, and
    also when the relay entry backing one IS present but stale."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    no_worktree_rows = board_cli._build(
        [{"id": "t1", "status": "queued"}],
        machine="m1",
        recent_mins=120,
    )
    assert no_worktree_rows[0]["length_display"] is None

    stale_rows = board_cli._build(
        [{
            "id": "t2", "status": "started", "repo": "github.com/example/repo",
            "owner": "m1/wt1", "target_worktree": "wt1",
        }],
        machine="m1",
        recent_mins=120,
        relay_fetch_many=lambda refs: {
            ("github.com/example/repo", "wt1"): {
                "repo": "github.com/example/repo",
                "worktree_id": "wt1",
                "fetched_at": 900.0,
                "poll_interval_seconds": 10.0,
                "bundle": {
                    "facts": {
                        "session_length": {
                            "confirmed": True,
                            "observed_at": 900.0,
                            "value": {"session_count": 3, "turn_count": 25},
                        },
                    }
                },
            }
        },
    )
    assert stale_rows[0]["length_display"] is None


def test_build_stale_relay_renders_unknown_worktree_status(monkeypatch):
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [{
            "id": "t1",
            "status": "started",
            "repo": "github.com/example/repo",
            "owner": "m1/wt1",
            "target_worktree": "wt1",
        }],
        machine="m1",
        recent_mins=120,
        relay_fetch_many=lambda refs: {
            ("github.com/example/repo", "wt1"): {
                "repo": "github.com/example/repo",
                "worktree_id": "wt1",
                "fetched_at": 900.0,
                "poll_interval_seconds": 10.0,
                "bundle": {
                    "facts": {
                        "claims": {
                            "confirmed": True,
                            "observed_at": 900.0,
                            "value": {"resources": [{"kind": "pr"}], "owner_ref": None},
                        }
                    }
                },
            }
        },
    )
    assert rows[0]["artifacts_summary"] == "stale/unknown"
    assert rows[0]["worktree_status"]["status"] == "stale"
    assert "relay stale or cold" in rows[0]["worktree_status"]["body"]


def test_main_reads_local_coordinator(monkeypatch, tmp_path, capsys):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 1234}}),
        encoding="utf-8",
    )
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MACHINE=m1\n", encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps([{"id": "t1", "status": "queued"}]).encode()

    captured = {"urls": []}

    def open_request(request, timeout):
        captured["urls"].append(request.full_url)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(board_cli.urllib.request, "urlopen", open_request)
    assert board_cli.main(["--machine", "m1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "t1"
    assert captured["urls"][0].startswith("http://127.0.0.1:1234/tasks?")
    assert captured["timeout"] == 3


def test_endpoint_maps_wildcard_bind_to_loopback(monkeypatch, tmp_path):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "0.0.0.0", "port": "4321"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path))
    assert board_cli._endpoint() == "http://127.0.0.1:4321"


def test_local_machine_reads_persisted_alias_before_hostname(monkeypatch, tmp_path):
    (tmp_path / "machine").write_text("box1", encoding="utf-8")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(board_cli.platform, "node", lambda: "CPC-tmich-OIXUI")
    assert board_cli._local_machine() == "box1"


def test_main_reports_missing_endpoint_without_traceback(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    assert board_cli.main(["--machine", "m1"]) == 1
    err = capsys.readouterr().err
    assert "coordinator endpoint is unavailable" in err
    assert "Traceback" not in err


def test_remote_machine_falls_back_to_full_cli(monkeypatch):
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    monkeypatch.setattr(
        board_cli, "_no_window_kwargs", lambda: {"creationflags": 123}
    )
    captured = {}

    def run(command, check, **kwargs):
        captured["command"] = command
        assert check is False
        assert kwargs["creationflags"] == 123
        assert isinstance(kwargs.get("env"), dict)
        return types.SimpleNamespace(returncode=7)

    monkeypatch.setattr(board_cli.subprocess, "run", run)
    assert board_cli.main(["--machine", "m2"]) == 7
    assert captured["command"][1:7] == [
        "-m",
        "agent_dispatch",
        "inbox",
        "--machine",
        "m2",
        "--board",
    ]


def _run_stream_capture(argv: list[str]) -> tuple[int, list[dict]]:
    """Run ``board_cli.main(argv)`` capturing the raw ``sys.__stdout__``
    envelope (the stream path writes to the real stdout stream, which capsys
    does not intercept -- same convention as agent-codespaces' ``finalize
    --picker-progress`` test helper) and return ``(rc, frames)``."""
    import io

    buf = io.StringIO()
    with mock.patch.object(sys, "__stdout__", buf):
        rc = board_cli.main(argv)
    frames = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    return rc, frames


def test_stream_emits_begin_row_done_envelope(monkeypatch):
    """D2 (Phase 1): ``--stream`` wraps the board as the registered-pivot
    NDJSON envelope -- begin -> a row per task -> done -- one JSON object per
    line, so `tasks.py`'s streaming consumer can paint progressively."""
    monkeypatch.setattr(
        board_cli, "_fetch_rows",
        lambda args: [{"id": "t1", "group": "Queued"}, {"id": "t2", "group": "Started"}],
    )
    rc, frames = _run_stream_capture(["--machine", "m1", "--stream"])
    assert rc == 0
    assert [frame["type"] for frame in frames] == ["begin", "row", "row", "done"]
    assert frames[0]["count"] == 2
    assert [frame["entry"]["id"] for frame in frames[1:3]] == ["t1", "t2"]
    assert frames[3]["count"] == 2


def test_stream_without_subscribe_exits_after_one_shot(monkeypatch):
    """Without ``--subscribe``, ``--stream`` never loops -- it is a one-shot
    NDJSON-framed fetch, same cardinality as the plain JSON path."""
    calls = {"n": 0}

    def fetch(args):
        calls["n"] += 1
        return []

    monkeypatch.setattr(board_cli, "_fetch_rows", fetch)
    rc, _frames = _run_stream_capture(["--machine", "m1", "--stream"])
    assert rc == 0
    assert calls["n"] == 1


def test_stream_error_frame_on_initial_fetch_failure(monkeypatch):
    """A failed initial fetch under ``--stream`` emits an ``error`` frame and
    exits 1 -- the NDJSON-framed equivalent of the one-shot path's stderr +
    exit-1 contract, so a streaming pivot can distinguish this from an empty
    board."""
    def fetch(args):
        raise RuntimeError("coordinator unavailable")

    monkeypatch.setattr(board_cli, "_fetch_rows", fetch)
    rc, frames = _run_stream_capture(["--machine", "m1", "--stream"])
    assert rc == 1
    assert frames == [{"type": "error", "message": "coordinator unavailable"}]


def test_subscribe_emits_delta_and_removed_frames(monkeypatch):
    """D2: ``--subscribe`` holds the channel open, re-fetching on
    ``--interval`` and diffing against the last snapshot -- a changed row
    becomes a ``delta``, a vanished one becomes ``removed``, and an
    unreachable channel (``KeyboardInterrupt``, standing in for the Picker
    closing the pipe) ends the loop cleanly."""
    snapshots = [
        [{"id": "t1", "group": "Queued"}, {"id": "t2", "group": "Started"}],
        [{"id": "t1", "group": "Started"}],  # t1 changed, t2 removed
    ]
    calls = {"n": 0}

    def fetch(args):
        if calls["n"] < len(snapshots):
            snapshot = snapshots[calls["n"]]
            calls["n"] += 1
            return snapshot
        raise KeyboardInterrupt

    monkeypatch.setattr(board_cli, "_fetch_rows", fetch)
    monkeypatch.setattr(board_cli.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(
        ["--machine", "m1", "--stream", "--subscribe"]
    )
    assert rc == 0
    types_seen = [frame["type"] for frame in frames]
    assert types_seen == ["begin", "row", "row", "done", "delta", "removed"]
    delta = next(f for f in frames if f["type"] == "delta")
    assert delta["entry"]["id"] == "t1"
    assert delta["entry"]["group"] == "Started"
    removed = next(f for f in frames if f["type"] == "removed")
    assert removed["id"] == "t2"


def test_subscribe_skips_transient_fetch_failure(monkeypatch):
    """A re-scan failure during ``--subscribe`` (coordinator hiccup) must not
    kill the live channel -- it skips that tick and tries again next time."""
    calls = {"n": 0}

    def fetch(args):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "t1", "group": "Queued"}]
        if calls["n"] == 2:
            raise RuntimeError("transient hiccup")
        raise KeyboardInterrupt

    monkeypatch.setattr(board_cli, "_fetch_rows", fetch)
    monkeypatch.setattr(board_cli.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(
        ["--machine", "m1", "--stream", "--subscribe"]
    )
    assert rc == 0
    # No delta/removed/error frame from the skipped tick -- just the initial
    # begin/row/done envelope.
    assert [frame["type"] for frame in frames] == ["begin", "row", "done"]
    assert calls["n"] == 3


def test_diff_rows_detects_changes_and_removals():
    prev = [{"id": "a", "v": 1}, {"id": "b", "v": 1}]
    curr = [{"id": "a", "v": 2}, {"id": "c", "v": 1}]
    deltas, removed = board_cli._diff_rows(prev, curr)
    assert deltas == [{"id": "a", "v": 2}, {"id": "c", "v": 1}]
    assert removed == ["b"]


def test_fetch_rows_direct_delegates_to_build(monkeypatch):
    """``_fetch_rows`` (the --stream/--subscribe fetch path) resolves to the
    direct-coordinator path on this machine, running the raw task list
    through the same `_build` the one-shot path uses."""
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    monkeypatch.setattr(board_cli, "_endpoint", lambda: "http://x:1")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps([{"id": "t1", "status": "queued"}]).encode()

    monkeypatch.setattr(
        board_cli.urllib.request, "urlopen", lambda request, timeout: Response()
    )
    args = types.SimpleNamespace(
        machine="m1", recent_mins=120, label=None, limit=200,
    )
    rows = board_cli._fetch_rows(args)
    assert rows[0]["id"] == "t1"


def test_fetch_rows_delegated_parses_subprocess_json(monkeypatch):
    """Cross-machine ``_fetch_rows`` capture-parses the delegated inbox
    subprocess's JSON-array stdout (rather than forwarding it verbatim, as
    the plain one-shot path does) so the --stream path can frame it."""
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    monkeypatch.setattr(
        board_cli, "_no_window_kwargs", lambda: {}
    )

    def run(command, check, env, capture_output, text, **kwargs):
        assert capture_output is True
        assert text is True
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"id": "t9"}]),
            stderr="",
        )

    monkeypatch.setattr(board_cli.subprocess, "run", run)
    args = types.SimpleNamespace(
        machine="m2", recent_mins=120, label=None, limit=200,
    )
    rows = board_cli._fetch_rows(args)
    assert rows == [{"id": "t9"}]


def test_fetch_rows_delegated_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    monkeypatch.setattr(board_cli, "_no_window_kwargs", lambda: {})

    def run(command, check, env, capture_output, text, **kwargs):
        return types.SimpleNamespace(returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr(board_cli.subprocess, "run", run)
    args = types.SimpleNamespace(
        machine="m2", recent_mins=120, label=None, limit=200,
    )
    try:
        board_cli._fetch_rows(args)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "boom" in str(exc)


def _install_fake_board_relay(monkeypatch, fake_module) -> None:
    """Patch ``agent_dispatch.board_relay`` for ``board_cli``'s own ``from .
    import board_relay`` to pick up. Patching only ``sys.modules`` is not
    enough once any other already-collected test module has imported the
    real ``board_relay`` first: Python's ``from package import submodule``
    skips re-resolving via ``sys.modules`` whenever the package object
    already carries a ``submodule`` attribute (set as a side effect of that
    earlier real import), so the package attribute must be patched too."""
    monkeypatch.setitem(sys.modules, "agent_dispatch.board_relay", fake_module)
    monkeypatch.setattr(agent_dispatch, "board_relay", fake_module, raising=False)


def test_run_stream_uses_relay_for_direct_subscribe_path(monkeypatch):
    """Phase 3a: the direct (local) ``--subscribe`` path hands off to
    ``board_relay.run_relay`` instead of the legacy poll loop."""
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    monkeypatch.setattr(board_cli, "_fetch_rows", lambda args: [{"id": "t1"}])

    fake_board_relay = types.ModuleType("agent_dispatch.board_relay")

    class RelayUnavailable(Exception):
        pass

    recorded = {}

    def run_relay(args, out, *, initial_rows, interval):
        recorded["initial_rows"] = initial_rows
        recorded["interval"] = interval
        return 0

    fake_board_relay.RelayUnavailable = RelayUnavailable
    fake_board_relay.run_relay = run_relay
    _install_fake_board_relay(monkeypatch, fake_board_relay)

    rc = board_cli.main(["--machine", "m1", "--stream", "--subscribe"])

    assert rc == 0
    assert recorded["initial_rows"] == [{"id": "t1"}]
    assert recorded["interval"] == board_cli.DEFAULT_SUBSCRIBE_INTERVAL


def test_run_stream_falls_back_to_poll_loop_when_relay_unavailable(monkeypatch):
    """A daemon that doesn't advertise ready-frame support (``run_relay``
    raises ``RelayUnavailable``) falls back to the unmodified poll loop for
    this connection's whole lifetime, rather than erroring out."""
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")

    calls = {"n": 0}

    def fetch(args):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "t1"}]
        raise KeyboardInterrupt

    monkeypatch.setattr(board_cli, "_fetch_rows", fetch)
    monkeypatch.setattr(board_cli.time, "sleep", lambda _secs: None)

    fake_board_relay = types.ModuleType("agent_dispatch.board_relay")

    class RelayUnavailable(Exception):
        pass

    def run_relay(args, out, *, initial_rows, interval):
        raise RelayUnavailable("daemon too old")

    fake_board_relay.RelayUnavailable = RelayUnavailable
    fake_board_relay.run_relay = run_relay
    _install_fake_board_relay(monkeypatch, fake_board_relay)

    rc = board_cli.main(["--machine", "m1", "--stream", "--subscribe"])

    assert rc == 0
    assert calls["n"] == 2  # initial fetch + one poll_loop tick before KeyboardInterrupt


def test_run_stream_skips_relay_for_delegated_machine(monkeypatch):
    """Phase 3a scope: a delegated (cross-machine) ``--subscribe`` board
    never attempts the relay at all, even if ``board_relay`` is importable --
    this machine's own coordinator only describes *its own* tasks."""
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    calls = {"n": 0}

    def fetch(args):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "t1"}]
        raise KeyboardInterrupt

    monkeypatch.setattr(board_cli, "_fetch_rows", fetch)
    monkeypatch.setattr(board_cli.time, "sleep", lambda _secs: None)

    fake_board_relay = types.ModuleType("agent_dispatch.board_relay")

    def run_relay(*_args, **_kwargs):
        raise AssertionError("the relay must never be attempted for a delegated board")

    fake_board_relay.RelayUnavailable = Exception
    fake_board_relay.run_relay = run_relay
    _install_fake_board_relay(monkeypatch, fake_board_relay)

    rc = board_cli.main(["--machine", "m2", "--stream", "--subscribe"])

    assert rc == 0
    assert calls["n"] == 2
