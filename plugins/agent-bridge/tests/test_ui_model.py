"""Behavior of the /ui page's data model (ui_static/model.js), run under node.

The model is pure (no DOM), so these tests import it as an ES module and check
what the board and session viewer would show for real-shaped inputs.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

MODEL = Path(str(resources.files("agent_bridge").joinpath("ui_static", "model.js")))


def _run(tmp_path: Path, body: str) -> object:
    script = tmp_path / "t.mjs"
    script.write_text(
        f"import * as m from {json.dumps(MODEL.resolve().as_uri())};\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    res = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


NOW = 1_790_400_000


def _live(sid, **kw):
    base = {"session_id": sid, "status": "live", "machine": "m1", "updated_at": NOW - 5,
            "registered_at": NOW - 3600}
    base.update(kw)
    return base


def test_worker_joins_its_supervising_worktree_as_one_task(tmp_path) -> None:
    live = [
        _live("orch", worktree_id="m1-win-20260925-114307-f0c7", repo="harness", turn_state="idle"),
        _live("old", worktree_id="m1-win-20260925-114307-f0c7", repo="harness"),
        _live("work", repo="app", worktree_id="anchor-app@cs1",
              venue={"kind": "codespace", "target": "ceo-list-blank-x6pr995j4gwc6vxp",
                     "supervisor_ref": "m1/harness/m1-win-20260925-114307-f0c7"}),
    ]
    out = _run(tmp_path, f"return m.buildTasks({json.dumps(live)}, [], {{}}, {NOW});")
    assert len(out) == 1
    task = out[0]
    assert task["key"] == "m1-win-20260925-114307-f0c7"
    assert [x["role"] for x in task["sessions"]] == ["worker", "orchestrator", "previous"]
    assert task["title"] == "ceo list blank"
    assert task["repos"] == ["harness", "app"]
    assert task["venues"] == ["codespace"]


def test_a_blocker_counts_until_the_session_works_past_it(tmp_path) -> None:
    blocked = {"ts": NOW - 3300, "phase": "blocked", "blocker": "the host must requeue policy 22464",
               "pr": "2404670", "markers": {"pr-build": "failed"}}
    out = _run(tmp_path, f"""
      const waiting = {json.dumps(_live("a", turn_state="idle", liveness="idle",
                                        last_activity_at=NOW - 3200, latest_progress=blocked))};
      const moved_on = {json.dumps(_live("b", turn_state="running", liveness="active",
                                         last_activity_at=NOW - 20, latest_progress=blocked))};
      return [m.sessionBucket(waiting, {NOW}), m.sessionBucket(moved_on, {NOW}),
              m.milestoneStale(moved_on, {NOW})];
    """)
    assert out == ["needs", "working", True]


def test_waiting_on_running_builds_is_monitoring_not_needs_you(tmp_path) -> None:
    prog = {"ts": NOW - 60, "phase": "blocked", "pr": "1",
            "blocker": "six blocking builds are still running", "markers": {"pr-build": "running"}}
    ask = dict(prog, blocker="builds are running; please approve the PR")
    out = _run(tmp_path, f"""
      const s = (p) => ({{ status: "live", turn_state: "idle", last_activity_at: {NOW - 50}, latest_progress: p }});
      return [m.sessionBucket(s({json.dumps(prog)}), {NOW}), m.sessionBucket(s({json.dumps(ask)}), {NOW})];
    """)
    assert out == ["monitoring", "needs"]


def test_turn_ends_between_tool_rounds_do_not_flip_a_task_to_idle(tmp_path) -> None:
    out = _run(tmp_path, f"""
      const s = {{ status: "live", turn_state: "idle", liveness: "idle", last_activity_at: {NOW - 30} }};
      return [m.sessionBucket(s, {NOW}), m.sessionBucket(s, {NOW} + m.WORKING_GRACE + 1)];
    """)
    assert out == ["working", "idle"]


def test_board_order_is_stable_across_heartbeats(tmp_path) -> None:
    rows = [_live(f"s{i}", worktree_id=f"w{i}", turn_state="running", registered_at=NOW - 100 * i)
            for i in range(3)]
    out = _run(tmp_path, f"""
      const rows = {json.dumps(rows)};
      const a = m.buildTasks(rows, [], {{}}, {NOW}).map((t) => t.key);
      rows[2].updated_at = {NOW}; rows[0].updated_at = {NOW - 500};
      const b = m.buildTasks(rows, [], {{}}, {NOW}).map((t) => t.key);
      return [a, b];
    """)
    assert out[0] == out[1] == ["w0", "w1", "w2"]


def test_earlier_tasks_come_from_workspaces_with_live_pr_state(tmp_path) -> None:
    workspaces = [
        {"id": "m1-win-20260925-125810-9a13", "project": "ext", "status": "active",
         "branch": "worktree/m1-win-20260925-125810-9a13", "started_at": "2026-09-25T12:58:16",
         "pr": {"number": 3686, "url": "https://github.com/o/ext/pull/3686", "state": "merged",
                "title": "Fix bridge liveness after steer", "live": True}},
        {"id": "m1-win-20260925-160507-61dd", "project": "ext", "status": "active",
         "pr": {"number": 3693, "url": "https://github.com/o/ext/pull/3693", "state": "open"}},
        {"id": "m1-win-20260925-200000-aaaa", "project": "ext", "status": "active",
         "subject": "Teach the picker to wrap long titles", "session_count": 2},
        {"id": "m1-win-20260925-210000-bbbb", "project": "ext", "status": "active",
         "session_count": 0, "turn_count": 0},
        {"id": "h-k", "project": "notes", "status": "active", "pair_id": "p1", "pair_role": "knowledge"},
        {"id": "h", "project": "harness", "status": "active", "pair_id": "p1", "pair_role": "harness",
         "title": "Harness task"},
        {"id": "sys", "project": "ext", "status": "active", "origin": "system"},
    ]
    out = _run(tmp_path, f"""
      return m.buildTasks([], {json.dumps(workspaces)}, {{}}, {NOW})
        .map((t) => [t.key, t.bucket, t.title, t.progress && t.progress.summary, t.pr && t.pr.state,
                     (t.paired || []).map((p) => p.id)]);
    """)
    rows = {r[0]: r for r in out}
    assert set(rows) == {"m1-win-20260925-125810-9a13", "m1-win-20260925-160507-61dd",
                         "m1-win-20260925-200000-aaaa", "m1-win-20260925-210000-bbbb", "h"}
    assert all(r[1] == "earlier" for r in out)
    assert rows["m1-win-20260925-125810-9a13"][2:5] == ["Fix bridge liveness after steer", "Opened PR 3686", "merged"]
    # A recorded (not re-checked) state is not shown: it goes stale once a PR merges.
    assert rows["m1-win-20260925-160507-61dd"][4] is None
    assert rows["m1-win-20260925-200000-aaaa"][2] == "Teach the picker to wrap long titles"
    assert rows["m1-win-20260925-210000-bbbb"][3] == "No sessions yet"
    assert rows["h"][5] == ["h-k"]


def test_a_launch_shows_as_starting_until_its_session_registers(tmp_path) -> None:
    pending = {"w9": {"title": "Fix it", "project": "harness", "prompt": "Fix it now", "at": NOW}}
    out = _run(tmp_path, f"""
      const before = m.buildTasks([], [], {json.dumps(pending)}, {NOW});
      const after = m.buildTasks([{json.dumps(_live("s9", worktree_id="w9", turn_state="running"))}], [],
                                 {json.dumps(pending)}, {NOW});
      return [before.map((t) => [t.key, t.bucket, t.title]), after.map((t) => [t.key, t.bucket])];
    """)
    assert out == [[["w9", "starting", "Fix it"]], [["w9", "working"]]]


def test_filters_match_text_venue_and_worker_repo(tmp_path) -> None:
    live = [
        _live("orch", worktree_id="w1", repo="harness"),
        _live("work", repo="app", branch="user/me/fix-login",
              venue={"kind": "codespace", "target": "cs-1", "supervisor_ref": "m1/harness/w1"}),
        _live("solo", worktree_id="w2", repo="ext"),
    ]
    out = _run(tmp_path, f"""
      const t = m.buildTasks({json.dumps(live)}, [], {{}}, {NOW});
      const keys = (f) => m.filterTasks(t, f).map((x) => x.key).sort();
      return [keys({{ repo: "app" }}), keys({{ venue: "codespace" }}), keys({{ venue: "local" }}),
              keys({{ q: "fix-login" }}), keys({{ q: "nothing here" }})];
    """)
    assert out == [["w1"], ["w1"], ["w2"], ["w1"], []]


def test_session_model_folds_real_server_sse_frames(tmp_path) -> None:
    """End to end on the wire format: SDK events -> bridge translator -> EventLog ->
    the server's own SSE framing -> the page's parseSseBlock + SessionModel."""
    from agent_bridge.events import EventLog
    from agent_bridge.live_representation import translate_sdk_event
    from agent_bridge.routes.live_sessions import _RepresentedSession
    from agent_bridge.routes.sessions import _sse_event_stream

    log = EventLog(session_id="sim-1")
    sdk = [
        ("user.message", {"content": "Add a haiku"}),
        ("assistant.reasoning", {"content": "**Plan** it"}),
        ("tool.execution_start", {"toolCallId": "t1", "toolName": "bash",
                                  "arguments": {"command": "git status", "description": "Check the tree"}}),
        ("tool.execution_complete", {"toolCallId": "t1", "success": True, "result": {"content": "On branch main"}}),
        ("tool.execution_start", {"toolCallId": "t2", "toolName": "view", "arguments": {"path": "/r/src/a.ts"}}),
        ("tool.execution_complete", {"toolCallId": "t2", "success": False, "error": {"message": "no such file"}}),
        ("assistant.turn_end", {}),
        ("session.compaction_start", {"conversationTokens": 90000, "systemTokens": 4000}),
        ("session.compaction_complete", {"success": True, "tokensRemoved": 61000,
                                         "postCompactionTokens": 33000}),
        ("assistant.message", {"content": "done <b>not html</b>"}),
        ("assistant.turn_end", {}),
    ]
    for sdk_type, data in sdk:
        for event_type, payload in translate_sdk_event(sdk_type, data):
            log.append(event_type, payload)
    shim = _RepresentedSession(session_id="sim-1", event_log=log)

    async def frames(n):
        out = []
        gen = _sse_event_stream(shim, 0, server=None, is_disconnected=None, mgr=None)
        async for chunk in gen:
            if chunk.startswith("id:"):
                out.append(chunk)
            if len(out) >= n:
                break
        await gen.aclose()
        return out

    wire = asyncio.run(frames(len(log._events)))
    out = _run(tmp_path, f"""
      const model = new m.SessionModel();
      for (const f of {json.dumps(wire)}) {{
        const ev = m.parseSseBlock(f.replace(/\\n\\n$/, ""));
        if (ev.data) model.apply(ev.type, ev.data, ev.ts, ev.id);
      }}
      const work = model.blocks[1];
      return {{
        lastId: model.lastId, types: model.blocks.map((b) => b.type),
        user: model.blocks[0].text, note: model.blocks[2].text, agent: model.blocks[3].text,
        steps: work.steps.map((s) => [s.kind, s.verb || "", s.label || s.text, s.status || ""]),
        summary: m.summarizeSteps(work.steps),
        md: m.parseMarkdown(model.blocks[3].text),
      }};
    """)
    assert out["lastId"] == len(wire)
    assert out["types"] == ["user", "work", "note", "agent"]
    assert re.fullmatch(r"Context compacted: 61\D?000 tokens freed", out["note"])
    assert out["user"] == "Add a haiku"
    assert out["steps"] == [
        ["thought", "", "**Plan** it", ""],
        ["tool", "Ran", "Check the tree", "completed"],
        ["tool", "Read", "a.ts", "failed"],
    ]
    assert out["summary"] == ["Ran 1 command", "Read 1 file"]
    # Markup in content stays literal text: the parser only yields plain tokens.
    assert out["md"] == [{"t": "p", "inl": [{"t": "text", "text": "done <b>not html</b>"}]}]


def test_markdown_links_are_http_only(tmp_path) -> None:
    out = _run(tmp_path, r"""
      return m.parseInline("see [x](javascript:alert(1)) and [ok](https://example.com/a) or https://b.c/d.")
        .map((t) => [t.t, t.href || null]);
    """)
    assert ["link", "https://example.com/a"] in out and ["link", "https://b.c/d"] in out
    assert not any(t == "link" and h and not h.startswith("https://") for t, h in out)
