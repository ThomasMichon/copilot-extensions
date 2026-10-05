"""Tests for the D2 NDJSON streaming (``--stream``/``--subscribe``) path on
``agent-bridge agents`` (pivot-streaming-transport Phase 2): the Bridges
pivot's own low-risk first adoption of the registered-pivot ``stream``/
``subscribe`` contract, mirroring agent-dispatch's Phase 1
(``board_cli._run_stream``).
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace
from unittest import mock

import agent_bridge.inventory_cli as ic


def _run_stream_capture(args: SimpleNamespace) -> tuple[int, list[dict]]:
    """Run ``ic._run_agents_stream(args)`` capturing the raw ``sys.__stdout__``
    envelope (the stream path writes to the real stdout stream, which capsys
    does not intercept) and return ``(rc, frames)``."""
    buf = io.StringIO()
    with mock.patch.object(sys, "__stdout__", buf):
        rc = ic._run_agents_stream(args)
    frames = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    return rc, frames


def _args(**kwargs) -> SimpleNamespace:
    defaults = dict(
        stream=True, subscribe=False, interval=2.0,
        all_projects=True, json=True,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_stream_emits_begin_row_done_envelope(monkeypatch):
    monkeypatch.setattr(
        ic, "_fetch_agent_rows",
        lambda args, **_kwargs: (
            [{"name": "a", "target_type": "ssh"}, {"name": "b", "target_type": "local"}],
            [], True,
        ),
    )
    rc, frames = _run_stream_capture(_args())
    assert rc == 0
    assert [f["type"] for f in frames] == ["begin", "row", "row", "done"]
    assert frames[0]["count"] == 2
    assert [f["entry"]["name"] for f in frames[1:3]] == ["a", "b"]
    assert frames[3]["count"] == 2


def test_stream_without_subscribe_fetches_once(monkeypatch):
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        calls["n"] += 1
        return [], [], True

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    rc, _frames = _run_stream_capture(_args())
    assert rc == 0
    assert calls["n"] == 1


def test_stream_error_frame_on_initial_fetch_failure(monkeypatch):
    def fetch(args, force_refresh=False, require_complete=False):
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    rc, frames = _run_stream_capture(_args())
    assert rc == 1
    assert frames == [{"type": "error", "message": "bridge unavailable"}]


def test_initial_scan_retries_past_incomplete_namespace(monkeypatch):
    """A reconnect (or first launch) has no prior snapshot to diff against
    -- publishing an incomplete initial roster as authoritative would make
    the Picker replace its whole cache with the smaller set, silently
    dropping the missing namespaced agents with no `removed` frame at all.
    A bounded retry gives a transient resolver hiccup a chance to clear
    before that first publish."""
    attempts = [
        ([{"name": "local-agent"}], ["codespace"], True),  # incomplete
        ([{"name": "codespace:x"}, {"name": "local-agent"}], [], True),  # clean
    ]
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        row = attempts[min(calls["n"], len(attempts) - 1)]
        calls["n"] += 1
        return row

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args())
    assert rc == 0
    assert calls["n"] == 2
    names = {f["entry"]["name"] for f in frames if f["type"] == "row"}
    assert names == {"codespace:x", "local-agent"}


def test_initial_scan_publishes_after_exhausting_retries(monkeypatch):
    """An initial scan that stays incomplete across every retry attempt must
    still publish eventually (best-effort) rather than blocking forever --
    bounded means bounded."""
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        calls["n"] += 1
        return [{"name": "local-agent"}], ["codespace"], True

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args())
    assert rc == 0
    assert calls["n"] == ic.INITIAL_SCAN_MAX_RETRIES + 1
    assert [f["type"] for f in frames] == ["begin", "row", "done"]


def test_initial_scan_never_retries_capability_unknown(monkeypatch):
    """Retrying a capability-unknown (old) daemon can't resolve anything --
    there is no signal to wait for -- so it publishes on the first fetch."""
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        calls["n"] += 1
        return [{"name": "local-agent"}], [], False

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, _frames = _run_stream_capture(_args())
    assert rc == 0
    assert calls["n"] == 1


def test_initial_scan_503_on_intermediate_attempt_keeps_retrying(monkeypatch):
    """A `require_complete=True` `503` on a non-final attempt must be
    treated the same as "still incomplete" -- retried, not raised -- so a
    transient blip during the bounded retry window doesn't abort the whole
    initial scan early."""
    from agent_bridge.client import BridgeClientError

    attempts = [
        BridgeClientError(503, "nothing authoritative to serve"),
        ([{"name": "codespace:x"}, {"name": "local-agent"}], [], True),
    ]
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        outcome = attempts[min(calls["n"], len(attempts) - 1)]
        calls["n"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args())
    assert rc == 0
    assert calls["n"] == 2
    names = {f["entry"]["name"] for f in frames if f["type"] == "row"}
    assert names == {"codespace:x", "local-agent"}


def test_initial_scan_503_exhausted_surfaces_as_error(monkeypatch):
    """A `require_complete=True` daemon that never has anything
    authoritative to serve, across every bounded retry, must surface that
    `503` as this initial scan's own visible failure -- never silently
    publish a stale/partial roster as if it were complete."""
    from agent_bridge.client import BridgeClientError

    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        calls["n"] += 1
        raise BridgeClientError(503, "nothing authoritative to serve")

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args())
    assert rc == 1
    assert calls["n"] == ic.INITIAL_SCAN_MAX_RETRIES + 1
    assert frames[0]["type"] == "error"


def test_fetch_agent_rows_raises_on_topology_errors(monkeypatch):
    """A topology-profile error must not silently vanish under --stream --
    it's framed as the initial-fetch failure (same contract as a connection
    error), never swallowed."""
    class Client:
        def list_agents_with_incomplete(self, *, force_refresh=False, require_complete=False):
            return [{"name": "a", "project": None}], ["broken: machines.yaml"], [], True

    monkeypatch.setattr(ic._core(), "_get_client", lambda: Client())
    try:
        ic._fetch_agent_rows(SimpleNamespace(all_projects=True, json=True))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "broken: machines.yaml" in str(exc)


def test_fetch_agent_rows_returns_incomplete_namespaces(monkeypatch):
    """A namespace resolver timeout is NOT a topology error -- it's returned
    alongside the (partial) roster, not raised, since the roster itself is
    still usable; only the diff step needs to know about it."""
    class Client:
        def list_agents_with_incomplete(self, *, force_refresh=False, require_complete=False):
            return [{"name": "a", "project": None}], [], ["codespace"], True

    monkeypatch.setattr(ic._core(), "_get_client", lambda: Client())
    rows, incomplete, known = ic._fetch_agent_rows(
        SimpleNamespace(all_projects=True, json=True)
    )
    assert rows == [{"name": "a", "project": None}]
    assert incomplete == ["codespace"]
    assert known is True


def test_subscribe_emits_delta_and_removed_frames(monkeypatch):
    snapshots = [
        ([{"name": "a", "target_type": "ssh"}, {"name": "b", "target_type": "local"}], [], True),
        ([{"name": "a", "target_type": "ssh2"}], [], True),  # a changed, b removed
    ]
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        if calls["n"] < len(snapshots):
            snapshot = snapshots[calls["n"]]
            calls["n"] += 1
            return snapshot
        raise KeyboardInterrupt

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args(subscribe=True))
    assert rc == 0
    types_seen = [f["type"] for f in frames]
    assert types_seen == ["begin", "row", "row", "done", "delta", "removed"]
    delta = next(f for f in frames if f["type"] == "delta")
    assert delta["entry"]["name"] == "a"
    assert delta["entry"]["target_type"] == "ssh2"
    removed = next(f for f in frames if f["type"] == "removed")
    assert removed["id"] == "b"


def test_subscribe_suppresses_removal_from_incomplete_namespace(monkeypatch):
    """A namespace resolver that times out on a `--subscribe` re-scan
    silently drops its agents from that one snapshot -- a diffing consumer
    must not treat that as a real removal, or the pivot flickers valid
    `codespace:`-namespaced agents in and out on every transient hiccup.
    The entry stays visible (carried forward, unchanged) until a later
    COMPLETE scan either confirms it gone or updates it."""
    snapshots = [
        ([{"name": "codespace:x", "v": 1}, {"name": "local-agent", "v": 1}], [], True),
        # Resolver timed out this tick: codespace:x silently absent, but
        # flagged incomplete -- must NOT be reported removed.
        ([{"name": "local-agent", "v": 1}], ["codespace"], True),
    ]
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        if calls["n"] < len(snapshots):
            snapshot = snapshots[calls["n"]]
            calls["n"] += 1
            return snapshot
        raise KeyboardInterrupt

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args(subscribe=True))
    assert rc == 0
    types_seen = [f["type"] for f in frames]
    # No `removed` frame for codespace:x, and no spurious `delta` either --
    # the second (incomplete) scan produced nothing new to report.
    assert types_seen == ["begin", "row", "row", "done"]


def test_subscribe_suppresses_all_namespaced_removals_when_capability_unknown(
    monkeypatch,
):
    """Against a daemon too old to advertise the incomplete-namespaces
    capability at all (``capability_known=False``), an empty
    `incomplete_namespaces` is NOT proof the scan was complete -- every
    namespaced (``prefix:name``) removal must be suppressed, not just ones
    the (unavailable) signal happens to name. A plain (non-namespaced) local
    agent's removal is unaffected -- it was never subject to the silent
    namespace-resolver-drop bug."""
    snapshots = [
        (
            [
                {"name": "codespace:x", "v": 1},
                {"name": "local-agent", "v": 1},
            ],
            [], False,
        ),
        ([], [], False),  # both silently absent -- daemon can't say why
    ]
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        if calls["n"] < len(snapshots):
            snapshot = snapshots[calls["n"]]
            calls["n"] += 1
            return snapshot
        raise KeyboardInterrupt

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args(subscribe=True))
    assert rc == 0
    removed_ids = {f["id"] for f in frames if f["type"] == "removed"}
    # codespace:x suppressed (namespaced, capability unknown); local-agent
    # DOES report removed (never namespaced, so never suppressed).
    assert removed_ids == {"local-agent"}


def test_subscribe_skips_transient_fetch_failure(monkeypatch):
    calls = {"n": 0}

    def fetch(args, force_refresh=False, require_complete=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"name": "a"}], [], True
        if calls["n"] == 2:
            raise RuntimeError("transient hiccup")
        raise KeyboardInterrupt

    monkeypatch.setattr(ic, "_fetch_agent_rows", fetch)
    monkeypatch.setattr(ic.time, "sleep", lambda _secs: None)
    rc, frames = _run_stream_capture(_args(subscribe=True))
    assert rc == 0
    assert [f["type"] for f in frames] == ["begin", "row", "done"]
    assert calls["n"] == 3


def test_diff_agent_rows_detects_changes_and_removals():
    prev = [{"name": "a", "v": 1}, {"name": "b", "v": 1}]
    curr = [{"name": "a", "v": 2}, {"name": "c", "v": 1}]
    deltas, removed = ic._diff_agent_rows(prev, curr)
    assert deltas == [{"name": "a", "v": 2}, {"name": "c", "v": 1}]
    assert removed == ["b"]


def test_cmd_agents_dispatches_to_stream_runner(monkeypatch):
    """``agents --stream`` must route through ``_run_agents_stream`` (and its
    exit code) rather than the plain print-and-return path."""
    called = {}

    def fake_stream(args):
        called["ran"] = True
        return 3

    monkeypatch.setattr(ic, "_run_agents_stream", fake_stream)
    try:
        ic._cmd_agents(SimpleNamespace(stream=True))
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert exc.code == 3
    assert called.get("ran") is True


def test_cmd_agents_without_stream_uses_plain_path(monkeypatch, capsys):
    """Without --stream, nothing changes from the pre-existing contract."""
    class Client:
        def list_agents_with_diagnostics(self):
            return [{"name": "a", "display_name": "a", "project": None}], []

    monkeypatch.setattr(ic._core(), "_get_client", lambda: Client())
    ic._cmd_agents(SimpleNamespace(json=True, all_projects=True, stream=False))
    out = capsys.readouterr().out
    assert '"a"' in out
