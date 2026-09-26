"""A venue session records the worktree that supervises it (handoff reattach)."""

from __future__ import annotations

import argparse
import json
import types

from agent_bridge import inventory_cli
from agent_bridge.models import LiveSessionVenue


def test_venue_round_trips_the_supervisor_ref():
    venue = LiveSessionVenue(kind="codespace", target="cs-1", mux_session_name="wt-a",
                             supervisor_ref="host/example-harness/wt-1")
    stored = json.loads(venue.model_dump_json())
    assert stored["supervisor_ref"] == "host/example-harness/wt-1"
    assert LiveSessionVenue(**stored).supervisor_ref == "host/example-harness/wt-1"


def test_older_venue_payloads_have_no_supervisor():
    venue = LiveSessionVenue(kind="ssh", target="box", mux_session_name="wt-b")
    assert venue.supervisor_ref is None


def _list(monkeypatch, capsys, supervisor):
    rows = [
        {"session_id": "s1", "venue": {"kind": "codespace", "target": "cs-1",
                                       "supervisor_ref": "host/example-harness/wt-1"}},
        {"session_id": "s2", "venue": {"kind": "container", "target": "box",
                                       "supervisor_ref": "host/example-harness/wt-2"}},
        {"session_id": "s3", "venue": None},
    ]
    client = types.SimpleNamespace(list_live_sessions=lambda **kw: rows)
    core = types.SimpleNamespace(_get_client=lambda: client,
                                 _json_out=lambda data: print(json.dumps(data)))
    monkeypatch.setattr(inventory_cli, "_core", lambda: core)
    inventory_cli._cmd_live_sessions(argparse.Namespace(
        live_action="list", worktree_id=None, include_dead=False, json=True,
        supervisor=supervisor))
    return [s["session_id"] for s in json.loads(capsys.readouterr().out)]


def test_list_filters_by_supervising_worktree(monkeypatch, capsys):
    assert _list(monkeypatch, capsys, "host/example-harness/wt-1") == ["s1"]


def test_supervisor_filter_ignores_a_session_suffix(monkeypatch, capsys):
    assert _list(monkeypatch, capsys, "host/example-harness/wt-2#sess-9") == ["s2"]


def test_no_supervisor_filter_lists_everything(monkeypatch, capsys):
    assert _list(monkeypatch, capsys, None) == ["s1", "s2", "s3"]
