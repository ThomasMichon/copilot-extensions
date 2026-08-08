"""Tests for the D2 NDJSON streaming runner in :class:`RegisteredPivotRuntime`.

Drive the runtime against a tiny fake provider script (invoked with this
interpreter) so the whole envelope -- begin/row/summary/delta/removed/done/error
-- and the one-shot fallbacks are exercised end-to-end without a real plugin
CLI on PATH.
"""

from __future__ import annotations

import sys
import time

from agent_worktrees.picker_tui import pivots, tasks

# A self-contained fake provider. ``sys.argv[1]`` selects the behaviour; the
# runtime appends ``--stream`` when the pivot opts into streaming, so each mode
# can branch on its presence to prove the streaming vs. one-shot contract.
_FAKE_PROVIDER = r'''
import sys, json, time
mode = sys.argv[1]
stream = "--stream" in sys.argv

def emit(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

if mode == "ndjson":
    if not stream:
        print(json.dumps([{"id": "a", "title": "A"}]))
        sys.exit(0)
    emit({"type": "begin", "count": 2})
    emit({"type": "row", "entry": {"id": "a", "title": "A"}})
    emit({"type": "row", "entry": {"id": "b", "title": "B"}})
    emit({"type": "summary", "summary": {"total": "2"}})
    emit({"type": "done", "count": 2})
elif mode == "unsupported":
    if stream:
        sys.stderr.write("error: unrecognized arguments: --stream\n")
        sys.exit(2)
    print(json.dumps({"entries": [{"id": "x", "title": "X"}], "summary": {"k": "v"}}))
elif mode == "plainarray":
    # Ignores --stream entirely and always prints a bare array.
    print(json.dumps([{"id": "p", "title": "P"}]))
elif mode == "delta":
    emit({"type": "row", "entry": {"id": "a", "title": "A"}})
    emit({"type": "row", "entry": {"id": "b", "title": "B"}})
    emit({"type": "delta", "entry": {"id": "a", "title": "A2"}})
    emit({"type": "removed", "id": "b"})
    emit({"type": "done"})
elif mode == "errorframe":
    emit({"type": "row", "entry": {"id": "a", "title": "A"}})
    emit({"type": "error", "message": "boom"})
elif mode == "subscribe":
    emit({"type": "row", "entry": {"id": "a", "title": "A"}})
    emit({"type": "delta", "entry": {"id": "a", "title": "A2"}})
    # Hold the channel open (bounded so a failed teardown still dies).
    for _ in range(300):
        time.sleep(0.1)
'''


def _make_pivot(tmp_path, mode, *, stream=True, subscribe=False):
    script = tmp_path / "fake_provider.py"
    script.write_text(_FAKE_PROVIDER, encoding="utf-8")
    data = {
        "label": "Fake",
        "list": [sys.executable, str(script), mode],
        "entry": {"id": "id", "title": "title"},
        "stream": stream,
        "subscribe": subscribe,
    }
    return pivots.parse_manifest(data, name="fake", source_path=str(script))


def _wait(rt, machine, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state, rows, err = rt.get(machine)
        if predicate(state, rows, err):
            return state, rows, err
        time.sleep(0.02)
    return rt.get(machine)


def _wait_ready(rt, machine, timeout=8.0):
    return _wait(rt, machine, lambda s, r, e: s in ("ready", "error"), timeout)


def test_streaming_paints_rows_and_summary(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_make_pivot(tmp_path, "ndjson"))
    rt.ensure(None)
    state, rows, _err = _wait_ready(rt, None)
    assert state == "ready"
    assert [r["id"] for r in rows] == ["a", "b"]
    assert rt.get_summary(None) == {"total": "2"}


def test_streaming_delta_and_removed_update_in_place(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_make_pivot(tmp_path, "delta"))
    rt.ensure(None)
    state, rows, _err = _wait_ready(rt, None)
    assert state == "ready"
    # 'a' upgraded in place via delta; 'b' removed.
    assert [(r["id"], r["title"]) for r in rows] == [("a", "A2")]


def test_streaming_error_frame_surfaces_error(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_make_pivot(tmp_path, "errorframe"))
    rt.ensure(None)
    # A row paints 'ready' first, then the terminal error frame flips to error.
    state, _rows, err = _wait(rt, None, lambda s, r, e: s == "error")
    assert state == "error"
    assert "boom" in err


def test_stream_unsupported_falls_back_to_one_shot(tmp_path):
    # Appending --stream makes the fake exit 2 (argparse); the runner must
    # re-run the one-shot list (no --stream) and parse its {entries, summary}.
    rt = tasks.RegisteredPivotRuntime(_make_pivot(tmp_path, "unsupported"))
    rt.ensure(None)
    state, rows, _err = _wait_ready(rt, None)
    assert state == "ready"
    assert [r["id"] for r in rows] == ["x"]
    assert rt.get_summary(None) == {"k": "v"}


def test_plain_array_output_falls_back_to_one_shot(tmp_path):
    # A stream:true pivot whose provider ignores --stream and prints a bare
    # array still resolves (via the no-envelope one-shot fallback).
    rt = tasks.RegisteredPivotRuntime(_make_pivot(tmp_path, "plainarray"))
    rt.ensure(None)
    state, rows, _err = _wait_ready(rt, None)
    assert state == "ready"
    assert [r["id"] for r in rows] == ["p"]


def test_non_stream_pivot_uses_one_shot_path(tmp_path):
    # stream:false keeps the original one-shot contract even against a provider
    # that could stream -- it runs the list with no --stream (bare array).
    rt = tasks.RegisteredPivotRuntime(_make_pivot(tmp_path, "ndjson", stream=False))
    rt.ensure(None)
    state, rows, _err = _wait_ready(rt, None)
    assert state == "ready"
    assert [r["id"] for r in rows] == ["a"]


def test_subscribe_live_delta_then_close_tears_down(tmp_path):
    rt = tasks.RegisteredPivotRuntime(
        _make_pivot(tmp_path, "subscribe", subscribe=True))
    rt.ensure(None)
    # First paint, then the live delta upgrades the row in place.
    _wait_ready(rt, None)
    _state, rows, _err = _wait(
        rt, None, lambda s, r, e: bool(r) and r[0].get("title") == "A2")
    assert [(r["id"], r["title"]) for r in rows] == [("a", "A2")]
    # Teardown kills the held child promptly and leaves nothing tracked.
    rt.close()
    assert rt._procs == []


# --- D4: run_action_stream (progress-reporting actions) --------------------

_ACTION_PROVIDER = r'''
import sys, json, time
mode = sys.argv[1]

def emit(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()

if mode == "progress":
    emit({"type": "progress", "pct": 0, "msg": "starting"})
    emit({"type": "progress", "pct": 50, "msg": "halfway"})
    emit({"type": "progress", "pct": 100, "msg": "finishing"})
    emit({"type": "done", "message": "recycled"})
elif mode == "error":
    emit({"type": "progress", "pct": 10, "msg": "trying"})
    emit({"type": "error", "message": "boom"})
elif mode == "plain":
    print("just a blob")   # no envelope
elif mode == "hang":
    emit({"type": "progress", "pct": 5, "msg": "working"})
    for _ in range(300):
        time.sleep(0.1)
'''


class _Action:
    def __init__(self, run):
        self.run = tuple(run)


def _action_pivot(tmp_path):
    return pivots.parse_manifest(
        {"label": "P", "list": ["true"], "entry": {"id": "id"}},
        name="p", source_path=str(tmp_path),
    )


def _action(tmp_path, mode):
    script = tmp_path / "fake_action.py"
    script.write_text(_ACTION_PROVIDER, encoding="utf-8")
    return _Action([sys.executable, str(script), mode])


def test_run_action_stream_reports_progress_then_done(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_action_pivot(tmp_path))
    frames = []
    ok, msg = rt.run_action_stream(
        _action(tmp_path, "progress"), {}, lambda pct, m: frames.append((pct, m)))
    assert ok is True
    assert msg == "recycled"
    assert frames[0] == (0.0, "starting")
    assert (50.0, "halfway") in frames
    assert frames[-1] == (100.0, "finishing")


def test_run_action_stream_surfaces_error_frame(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_action_pivot(tmp_path))
    ok, msg = rt.run_action_stream(_action(tmp_path, "error"), {}, lambda p, m: None)
    assert ok is False
    assert "boom" in msg


def test_run_action_stream_plain_output_falls_back(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_action_pivot(tmp_path))
    # No envelope, exit 0 -> treated as success with the output tail.
    ok, msg = rt.run_action_stream(_action(tmp_path, "plain"), {}, lambda p, m: None)
    assert ok is True


def test_run_action_stream_cancellation(tmp_path):
    rt = tasks.RegisteredPivotRuntime(_action_pivot(tmp_path))
    seen = []
    cancel = {"v": False}

    def on_frame(pct, m):
        seen.append((pct, m))
        cancel["v"] = True  # cancel right after the first frame

    ok, msg = rt.run_action_stream(
        _action(tmp_path, "hang"), {}, on_frame, should_cancel=lambda: cancel["v"])
    assert ok is False
    assert msg == "cancelled"
    assert rt._procs == []  # child killed + untracked

