from __future__ import annotations

import io
import json
import sys
import time
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

from agent_worktrees import delegate_cli
from agent_worktrees import list_cli
from agent_worktrees import list_views_cli
from agent_worktrees import sessions, tracking

_FRESH = {"refreshed_age_s": 1.0, "bound_s": 90.0}


def _row(
    worktree_id: str,
    *,
    machine: str,
    platform: str,
    status: str,
    caller_worktree: str | None = None,
    repo: str = "example",
) -> dict:
    row = {
        "id": worktree_id,
        "machine": machine,
        "platform": platform,
        "repo": repo,
        "path": f"/wt/{worktree_id}",
        "status": status,
    }
    if caller_worktree:
        row["caller_worktree"] = caller_worktree
    return row


def _record(
    worktree_id: str,
    *,
    machine: str,
    platform: str,
    status: str,
    caller_worktree: str | None = None,
) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=worktree_id,
        branch=f"worktree/{worktree_id}",
        worktree_path=f"/wt/{worktree_id}",
        repo="example",
        machine=machine,
        platform=platform,
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status=status,
        completed_at=None,
        caller_worktree=caller_worktree,
        sessions=[],
    )


def test_annotate_delegate_graph_marks_host_finalized_delegate_finalizable():
    host = _row(
        "atlas-core-wsl-20260101-host",
        machine="atlas-core",
        platform="wsl",
        status="finalized",
    )
    delegate = _row(
        "ember-linux-20260101-delegate",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree="atlas-core-wsl-20260101-host",
    )

    delegate_cli.annotate_delegate_graph(
        [host, delegate],
        reachable_hosts={
            ("atlas-core", "wsl"): True,
            ("ember", "linux"): True,
        },
    )

    assert delegate["caller_state"]["state"] == "resolved"
    assert delegate["caller_state"]["status"] == "finalized"
    assert delegate["delegate_finalizable"] is True
    assert delegate["delegate_finalizable_reason"] == "host-finalized"
    assert host["delegates"] == [{
        "worktree_id": "ember-linux-20260101-delegate",
        "machine": "ember",
        "platform": "linux",
        "path": "/wt/ember-linux-20260101-delegate",
        "status": "active",
        "finalizable": True,
        "reason": "host-finalized",
    }]


def test_annotate_delegate_graph_keeps_active_host_blocking():
    host = _row(
        "atlas-core-wsl-20260101-host",
        machine="atlas-core",
        platform="wsl",
        status="active",
    )
    delegate = _row(
        "ember-linux-20260101-delegate",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree="atlas-core-wsl-20260101-host",
    )

    delegate_cli.annotate_delegate_graph([host, delegate])

    assert delegate["caller_state"]["state"] == "resolved"
    assert delegate["delegate_finalizable"] is False
    assert delegate["delegate_finalizable_reason"] == "host-active"


def test_annotate_delegate_graph_marks_missing_qualified_reachable_caller_gone():
    delegate = _row(
        "ember-linux-20260101-delegate",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree=tracking.format_claim_ref(
            "atlas-core", "example", "atlas-core-wsl-20260101-000000-abcd",
        ),
    )

    delegate_cli.annotate_delegate_graph(
        [delegate],
        reachable_hosts={
            ("atlas-core", "wsl"): True,
            ("ember", "linux"): True,
        },
    )

    assert delegate["caller_state"] == {
        "state": "gone",
        "worktree_id": "atlas-core-wsl-20260101-000000-abcd",
        "machine": "atlas-core",
        "platform": "wsl",
        "repo": "example",
    }
    assert delegate["delegate_finalizable"] is True
    assert delegate["delegate_finalizable_reason"] == "host-gone"


def test_annotate_delegate_graph_uses_qualified_source_platform_not_id_token():
    delegate = _row(
        "ember-linux-20260101-delegate",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree=tracking.format_claim_ref("atlas-core", "example", "opaque-caller"),
    )

    delegate_cli.annotate_delegate_graph(
        [delegate],
        reachable_hosts={
            ("atlas-core", "windows"): True,
            ("ember", "linux"): True,
        },
    )

    assert delegate["caller_state"]["state"] == "gone"
    assert delegate["caller_state"]["machine"] == "atlas-core"
    assert delegate["caller_state"]["platform"] == "windows"
    assert delegate["delegate_finalizable"] is True


@pytest.mark.parametrize("machine,platform,prefix", [
    ("host-wsl", "wsl", "host-wsl"),
    ("host-wsl", "wsl", "host-wsl-wsl"),
    ("host-win", "windows", "host-win"),
    ("host-windows", "windows", "host-windows"),
    ("host-linux", "linux", "host-linux"),
    ("host", "", "host"),
])
@pytest.mark.parametrize("reachable", [False, True])
def test_missing_environment_named_caller_preserves_reachability_gate(
    machine: str, platform: str, prefix: str, reachable: bool,
) -> None:
    identity = f"{prefix}-20261013-100000-abcd"
    delegate = _row(
        "worker-linux-20261013-100001-abcd",
        machine="worker", platform="linux", status="active",
        caller_worktree=tracking.format_claim_ref(machine, "example", identity),
    )

    delegate_cli.annotate_delegate_graph(
        [delegate], reachable_hosts={(machine, platform): reachable},
    )

    assert delegate["caller_state"]["machine"] == machine
    assert delegate["caller_state"].get("platform", "") == platform
    assert delegate["caller_state"]["state"] == ("gone" if reachable else "unreachable")
    assert delegate["delegate_finalizable"] is reachable
    assert delegate["delegate_finalizable_reason"] == (
        "host-gone" if reachable else "host-unreachable"
    )


def test_normalized_caller_does_not_choose_between_colliding_host_prefixes() -> None:
    delegate = _row(
        "worker-linux-20261013-100001-abcd",
        machine="worker", platform="linux", status="active",
        caller_worktree="host-wsl-20261013-100000-abcd",
    )
    delegate_cli.annotate_delegate_graph(
        [delegate], reachable_hosts={("host", "wsl"): True, ("host-wsl", "wsl"): False},
    )
    assert delegate["delegate_finalizable"] is False
    assert delegate["delegate_finalizable_reason"] == "host-unresolved"


def test_qualified_caller_does_not_choose_between_execution_environments() -> None:
    delegate = _row(
        "child", machine="worker", platform="linux", status="active",
        caller_worktree=tracking.format_claim_ref("host", "example", "opaque-parent"),
    )
    delegate_cli.annotate_delegate_graph(
        [delegate], reachable_hosts={("host", "windows"): True, ("host", "wsl"): True},
    )
    assert delegate["caller_state"]["state"] == "unresolved"
    assert delegate["delegate_finalizable"] is False


@pytest.mark.parametrize("caller", [
    "host-wsl-win-20261013-100000-abcd",
    "host-wsl-worker-wsl-20261013-100000-abcd",
    "host-wsl-20261013-worker-wsl-20261013-100000-abcd",
    "host-wsl-linux-20261013-100000-abcd",
    "host-wsl-20261013-100000-not-an-allocation",
])
def test_partial_machine_or_environment_match_never_authorizes_finalization(caller: str) -> None:
    delegate = _row(
        "worker-linux-20261013-100001-abcd",
        machine="worker", platform="linux", status="active", caller_worktree=caller,
    )
    delegate_cli.annotate_delegate_graph(
        [delegate], reachable_hosts={("host-wsl", "wsl"): True},
    )
    assert delegate["caller_state"]["state"] == "unresolved"
    assert delegate["delegate_finalizable"] is False
    assert delegate["delegate_finalizable_reason"] == "host-unresolved"


@pytest.mark.parametrize("prefix", ["host-wsl", "host-wsl-wsl"])
@pytest.mark.parametrize("suffix", ["abcd", "a" * 32])
def test_missing_paired_caller_id_does_not_prove_its_current_host(prefix: str, suffix: str) -> None:
    caller = f"{prefix}-20261013-100000-{suffix}-k"
    delegate = _row(
        "child", machine="worker", platform="linux", status="active", caller_worktree=caller,
    )
    delegate_cli.annotate_delegate_graph(
        [delegate], reachable_hosts={("host-wsl", "wsl"): True},
    )
    assert delegate["caller_state"]["state"] == "unresolved"
    assert delegate["delegate_finalizable"] is False


def test_qualified_missing_caller_uses_the_explicit_selector_not_allocation_syntax() -> None:
    delegate = _row(
        "child", machine="worker", platform="linux", status="active",
        caller_worktree=tracking.format_claim_ref("host-wsl", "example", "arbitrary-parent"),
    )
    hosts = {(f"host-{number}-wsl", "wsl"): True for number in range(50)}
    hosts[("host-wsl", "wsl")] = True
    delegate_cli.annotate_delegate_graph([delegate], reachable_hosts=hosts)
    assert delegate["caller_state"]["state"] == "gone"
    assert delegate["caller_state"]["machine"] == "host-wsl"
    assert delegate["delegate_finalizable"] is True


def test_build_list_json_payload_adds_delegate_annotations(monkeypatch):
    args = SimpleNamespace(
        mux_details=False,
        classify=False,
        tracking_status="all",
        include_other_platforms=False,
        all=True,
        profile_assignment_history=False,
    )
    host = _record(
        "atlas-core-wsl-20260101-host",
        machine="atlas-core",
        platform="wsl",
        status="finalized",
    )
    delegate = _record(
        "ember-linux-20260101-delegate",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree="atlas-core-wsl-20260101-host",
    )
    monkeypatch.setattr(
        list_cli.sessions,
        "scan_sessions_fast",
        lambda records: sessions.SessionContext(),
    )

    payload = list_cli._build_list_json_payload(args, [host, delegate], stamp_session_state=False)
    by_id = {row["id"]: row for row in payload["worktrees"]}

    assert by_id["ember-linux-20260101-delegate"]["caller_state"]["state"] == "resolved"
    assert by_id["ember-linux-20260101-delegate"]["delegate_finalizable"] is True
    assert by_id["atlas-core-wsl-20260101-host"]["delegates"][0]["worktree_id"] == (
        "ember-linux-20260101-delegate"
    )


def test_run_fleet_json_annotates_delegate_graph(monkeypatch):
    current = {
        "quality": "current", "observed_at": 1.0, "freshness": _FRESH,
        "roster_count": 1, "selected_count": 1,
    }
    probed = {list_views_cli.PROBE_STARTED_KEY: time.monotonic()}
    hosts = [
        {
            "machine": "atlas-core",
            "env": "wsl",
            "reachable": True,
            "projection": current,
            **probed,
            "worktrees": [_row(
                "atlas-core-wsl-20260101-host",
                machine="atlas-core",
                platform="wsl",
                status="finalized",
            )],
        },
        {
            "machine": "ember",
            "env": "linux",
            "reachable": True,
            "projection": current,
            **probed,
            "worktrees": [_row(
                "ember-linux-20260101-delegate",
                machine="ember",
                platform="linux",
                status="active",
                caller_worktree="atlas-core-wsl-20260101-host",
            )],
        },
    ]
    monkeypatch.setattr(
        list_views_cli,
        "_fleet_targets",
        lambda config: [
            ("atlas-core", "wsl", "atlas-core-wsl", "bash", True),
            ("ember", "linux", "ember", "bash", False),
        ],
    )
    monkeypatch.setattr(
        list_views_cli,
        "_probe_host",
        lambda *args, **kwargs: hosts.pop(0),
    )
    monkeypatch.setattr(
        list_views_cli.cfg,
        "load_config",
        lambda: SimpleNamespace(machine="atlas-core", platform="wsl", default_repo=SimpleNamespace(anchor=".")),
    )
    monkeypatch.setattr(list_views_cli.cfg, "project_name", lambda: "agent-worktrees")

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = list_views_cli.run_fleet(["--json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    delegate = payload["hosts"][1]["worktrees"][0]

    assert delegate["caller_state"]["state"] == "resolved"
    assert delegate["delegate_finalizable"] is True


_CURRENT = {"quality": "current", "observed_at": 1.0, "freshness": _FRESH}
_SAVED = {
    "projection": {"quality": "advisory", "observed_at": 1.0},
    "availability": {"status": "advisory", "reason": "resident not running"},
}
_DELEGATE_ID = "ember-linux-20260101-000001-abcd"
_CALLER_ID = "atlas-core-wsl-20260101-000000-abcd"
_FLEET_CONFIG = SimpleNamespace(
    machine="atlas-core", platform="wsl", default_repo=SimpleNamespace(anchor="."),
)


def _fleet_hosts(caller_host: dict, caller_rows: list[dict], *, probed: float | None = None) -> list[dict]:
    stamp = {list_views_cli.PROBE_STARTED_KEY: time.monotonic() if probed is None else probed}
    hosts = [
        {"machine": "atlas-core", "env": "wsl", "reachable": True,
         "worktrees": caller_rows, **stamp, **caller_host},
        {"machine": "ember", "env": "linux", "reachable": True, "projection": _CURRENT, **stamp,
         "worktrees": [_row(
             _DELEGATE_ID, machine="ember", platform="linux", status="active",
             caller_worktree=tracking.format_claim_ref("atlas-core", "example", _CALLER_ID),
         )]},
    ]
    for row in hosts:
        row.setdefault("reporting_context", {
            "version": 1, "status": "resolved", "hostname": "observed-host",
            "machine": row["machine"], "environment": row["env"],
            "execution_machine": row["machine"],
        })
        if isinstance(row.get("projection"), dict):
            row["projection"] = dict(row["projection"])
            row["projection"].setdefault("roster_count", len(row["worktrees"]))
            row["projection"].setdefault("selected_count", len(row["worktrees"]))
    return hosts


@pytest.mark.parametrize("caller_host,state,finalizable,reason", [
    ({"projection": _CURRENT}, "gone", True, "host-gone"),
    (_SAVED, "unknown", False, "host-roster-advisory"),
    ({"projection": _CURRENT, "availability": {"status": "stale", "reason": "x"}},
     "unknown", False, "host-roster-advisory"),
    ({}, "unknown", False, "host-roster-advisory"),
    ({"projection": {"quality": "current", "observed_at": 1.0}},
     "unknown", False, "host-roster-advisory"),
    ({"projection": {**_CURRENT, "freshness": {"refreshed_age_s": 91.0, "bound_s": 90.0}}},
     "unknown", False, "host-roster-advisory"),
    ({"projection": {**_CURRENT, "freshness": {"refreshed_age_s": None, "bound_s": 90.0}}},
     "unknown", False, "host-roster-advisory"),
    ({"projection": {**_CURRENT, "freshness": {"refreshed_age_s": True, "bound_s": 90.0}}},
     "unknown", False, "host-roster-advisory"),
    ({"projection": {**_CURRENT, "freshness": {"refreshed_age_s": float("nan"), "bound_s": 90.0}}},
     "unknown", False, "host-roster-advisory"),
    ({"projection": {**_CURRENT, "freshness": {"refreshed_age_s": -1.0, "bound_s": 90.0}}},
     "unknown", False, "host-roster-advisory"),
    ({"projection": _CURRENT, list_views_cli.PROBE_STARTED_KEY: None},
     "unknown", False, "host-roster-advisory"),
], ids=[
    "current", "saved", "stale", "unlabeled", "unproven-freshness", "overdue",
    "never-refreshed", "bool-age", "nan-age", "negative-age", "no-probe-clock",
])
def test_run_fleet_infers_gone_caller_only_from_current_roster(
    monkeypatch, caller_host, state, finalizable, reason,
):
    hosts = _fleet_hosts(caller_host, [])
    monkeypatch.setattr(
        list_views_cli, "_fleet_targets",
        lambda config: [
            ("atlas-core", "wsl", "atlas-core-wsl", "bash", True),
            ("ember", "linux", "ember", "bash", False),
        ],
    )
    monkeypatch.setattr(list_views_cli, "_probe_host", lambda *args, **kwargs: hosts.pop(0))
    monkeypatch.setattr(list_views_cli.cfg, "load_config", lambda: _FLEET_CONFIG)
    monkeypatch.setattr(list_views_cli.cfg, "project_name", lambda: "agent-worktrees")

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert list_views_cli.run_fleet(["--json"]) == 0
    delegate = json.loads(buf.getvalue())["hosts"][1]["worktrees"][0]

    assert delegate["caller_state"]["state"] == state
    assert delegate["caller_state"]["machine"] == "atlas-core"
    assert delegate["delegate_finalizable"] is finalizable
    assert delegate["delegate_finalizable_reason"] == reason


def test_saved_terminal_caller_never_authorizes_finalization():
    caller = _row(_CALLER_ID, machine="atlas-core", platform="wsl", status="finalized")
    hosts = delegate_cli.annotate_fleet_hosts(_fleet_hosts(_SAVED, [caller]))
    host_row = hosts[0]["worktrees"][0]
    delegate = hosts[1]["worktrees"][0]

    assert delegate["caller_state"]["state"] == "resolved"
    assert delegate["delegate_finalizable"] is False
    assert delegate["delegate_finalizable_reason"] == "host-roster-advisory"
    assert host_row["delegates"][0]["finalizable"] is False


def test_run_delegates_execute_skips_delegate_of_saved_caller_roster(monkeypatch):
    hosts = _fleet_hosts(_SAVED, [])
    monkeypatch.setattr(delegate_cli.cfg, "project_name", lambda: "agent-worktrees")
    monkeypatch.setattr(delegate_cli.cfg, "load_config", lambda: _FLEET_CONFIG)
    monkeypatch.setattr(
        delegate_cli.list_views_cli, "_fleet_targets",
        lambda config: [
            ("atlas-core", "wsl", "atlas-core-wsl", "bash", True),
            ("ember", "linux", "ember", "bash", False),
        ],
    )
    monkeypatch.setattr(
        delegate_cli.list_views_cli, "_probe_host", lambda *args, **kwargs: hosts.pop(0),
    )
    monkeypatch.setattr(
        delegate_cli, "_finalize_target",
        lambda target, **kwargs: pytest.fail(f"finalized {target['id']}"),
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert delegate_cli.run_delegates(["--json", "--execute"]) == 0
    payload = json.loads(buf.getvalue())

    assert payload["eligible"] == []
    assert [row["delegate_finalizable_reason"] for row in payload["blocked"]] == [
        "host-roster-advisory",
    ]
    assert payload["executed"] == []


def test_run_delegates_probes_passively_so_a_proven_current_roster_authorizes(monkeypatch):
    """The delegates snapshot reads rosters passively like `fleet`: only a
    passive reply carries the freshness proof, so a demand read would leave
    every caller `unknown` and nothing eligible."""
    hosts = _fleet_hosts({"projection": _CURRENT}, [])
    probes: list[list[str]] = []

    def _probe(*args, **kwargs):
        probes.append(list(kwargs["extra_args"]))
        return hosts.pop(0)

    monkeypatch.setattr(delegate_cli.cfg, "project_name", lambda: "agent-worktrees")
    monkeypatch.setattr(delegate_cli.cfg, "load_config", lambda: _FLEET_CONFIG)
    monkeypatch.setattr(
        delegate_cli.list_views_cli, "_fleet_targets",
        lambda config: [
            ("atlas-core", "wsl", "atlas-core-wsl", "bash", True),
            ("ember", "linux", "ember", "bash", False),
        ],
    )
    monkeypatch.setattr(delegate_cli.list_views_cli, "_probe_host", _probe)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert delegate_cli.run_delegates(["--json"]) == 0
    payload = json.loads(buf.getvalue())

    assert probes == [["--passive", "--all"], ["--passive", "--all"]]
    assert [row["id"] for row in payload["eligible"]] == [_DELEGATE_ID]
    assert payload["eligible"][0]["delegate_finalizable_reason"] == "host-gone"


def _stub_fleet(monkeypatch) -> None:
    monkeypatch.setattr(delegate_cli.cfg, "project_name", lambda: "agent-worktrees")
    monkeypatch.setattr(delegate_cli.cfg, "load_config", lambda: _FLEET_CONFIG)
    monkeypatch.setattr(
        delegate_cli.list_views_cli, "_fleet_targets",
        lambda config: [
            ("atlas-core", "wsl", "atlas-core-wsl", "bash", True),
            ("ember", "linux", "ember", "bash", False),
        ],
    )


def _run_delegates_json(argv: list[str]) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert delegate_cli.run_delegates(argv) == 0
    return json.loads(buf.getvalue())


@pytest.mark.parametrize("later_probe_s,state,finalizable", [
    (0.0, "gone", True),
    (120.0, "unknown", False),
], ids=["prompt", "slow-later-host"])
def test_slow_later_probe_expires_an_earlier_fresh_caller_roster(
    monkeypatch, later_probe_s, state, finalizable,
):
    """A resident's ``refreshed_age_s`` is frozen when it answers; local time
    spent probing later hosts still counts against the caller roster's bound."""
    clock = [1000.0]
    timer = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(list_views_cli, "time", timer)
    monkeypatch.setattr(delegate_cli, "time", timer)
    delegate_host = {"projection": _CURRENT, "worktrees": [_row(
        _DELEGATE_ID, machine="ember", platform="linux", status="active",
        caller_worktree=tracking.format_claim_ref("atlas-core", "example", _CALLER_ID),
    )]}

    def _run(cmd, **_kwargs):
        reply = {"worktrees": [], "projection": _CURRENT}
        if cmd[0] == "ssh":
            clock[0] += later_probe_s
            reply = delegate_host
        reply["projection"] = {
            **reply["projection"],
            "roster_count": len(reply["worktrees"]), "selected_count": len(reply["worktrees"]),
        }
        reply["reporting_context"] = {
            "version": 1, "status": "resolved", "hostname": "observed-host",
            "machine": "ember" if cmd[0] == "ssh" else "atlas-core",
            "environment": "linux" if cmd[0] == "ssh" else "wsl",
            "execution_machine": "ember" if cmd[0] == "ssh" else "atlas-core",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr="")

    monkeypatch.setattr(list_views_cli.subprocess, "run", _run)
    _stub_fleet(monkeypatch)
    payload = _run_delegates_json(["--json"])
    (row,) = payload["eligible"] + payload["blocked"]

    assert row["caller_state"]["state"] == state
    assert row["delegate_finalizable"] is finalizable
    assert all(list_views_cli.PROBE_STARTED_KEY not in host for host in payload["hosts"])
    # Serialized rows drop the probe clock, so an expired roster must say so itself.
    caller_host = payload["hosts"][0]
    if finalizable:
        assert "availability" not in caller_host
    else:
        assert caller_host["availability"]["status"] == "stale"


def test_execute_rechecks_caller_roster_freshness_before_each_finalize(monkeypatch):
    """A caller roster that authorized two delegates can outlive its bound
    while the first is finalized; the second is then left alone."""
    clock = [1000.0]
    monkeypatch.setattr(delegate_cli, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    second = "ember-linux-20260101-000002-abcd"
    hosts = _fleet_hosts({"projection": _CURRENT}, [], probed=1000.0)
    hosts[1]["worktrees"].append(_row(
        second, machine="ember", platform="linux", status="active",
        caller_worktree=tracking.format_claim_ref("atlas-core", "example", _CALLER_ID),
    ))
    hosts[1]["projection"].update(roster_count=2, selected_count=2)
    _stub_fleet(monkeypatch)
    monkeypatch.setattr(delegate_cli.list_views_cli, "_probe_host", lambda *a, **k: hosts.pop(0))
    finalized: list[str] = []

    def _finalize(target, **_kwargs):
        finalized.append(target["id"])
        clock[0] += 120.0
        return {"worktree_id": target["id"], "ok": True}

    monkeypatch.setattr(delegate_cli, "_finalize_target", _finalize)
    payload = _run_delegates_json(["--json", "--execute"])

    assert [row["id"] for row in payload["eligible"]] == [_DELEGATE_ID, second]
    assert finalized == [_DELEGATE_ID]
    assert [row["worktree_id"] for row in payload["errors"]] == [second]
    assert payload["errors"][0]["reason"] == "host-roster-advisory"


def test_cmd_list_stream_emits_classified_rows_progressively(monkeypatch):
    args = SimpleNamespace(
        mux_details=False,
        classify=True,
        profile_assignment_history=False,
    )
    records = [
        _record("wt-a", machine="m1", platform="wsl", status="active"),
        _record("wt-b", machine="m1", platform="wsl", status="active"),
    ]
    monkeypatch.setattr(
        list_cli.sessions,
        "scan_sessions_fast",
        lambda records: sessions.SessionContext(),
    )
    monkeypatch.setattr(list_cli.cfg, "load_config", lambda: SimpleNamespace(default_repo=SimpleNamespace()))
    monkeypatch.setattr(list_cli, "_build_active_paths", lambda records, session_ctx: {})
    monkeypatch.setattr(list_cli, "_worktree_to_dict", lambda rec, **kwargs: {"id": rec.worktree_id})
    monkeypatch.setattr(
        list_cli,
        "_classify_one_record",
        lambda rec, **kwargs: None if rec.worktree_id == "wt-a" else (_ for _ in ()).throw(RuntimeError("stop")),
    )

    with io.StringIO() as capture:
        monkeypatch.setattr(sys, "__stdout__", capture)
        try:
            list_cli._cmd_list_stream(args, records)
        except RuntimeError as exc:
            assert str(exc) == "stop"
        lines = [json.loads(line) for line in capture.getvalue().splitlines()]

    assert [line["type"] for line in lines[:3]] == ["begin", "worktree", "worktree"]
    assert lines[3]["phase"] == "classified"
    assert lines[3]["wt"]["id"] == "wt-a"


def test_run_delegates_execute_finalizes_only_eligible(monkeypatch):
    eligible = _row(
        "ember-linux-20260101-delegate",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree="atlas-core-wsl-20260101-host",
    )
    eligible["caller_state"] = {"state": "resolved", "worktree_id": "atlas-core-wsl-20260101-host"}
    eligible["delegate_finalizable"] = True
    eligible["delegate_finalizable_reason"] = "host-finalized"
    blocked = _row(
        "ember-linux-20260101-blocked",
        machine="ember",
        platform="linux",
        status="active",
        caller_worktree="atlas-core-wsl-20260101-live",
    )
    blocked["caller_state"] = {"state": "resolved", "worktree_id": "atlas-core-wsl-20260101-live"}
    blocked["delegate_finalizable"] = False
    blocked["delegate_finalizable_reason"] = "host-active"
    monkeypatch.setattr(
        delegate_cli,
        "_fleet_snapshot",
        lambda **kwargs: [
            {"machine": "atlas-core", "env": "wsl", "reachable": True,
             "projection": {**_CURRENT, "roster_count": 2, "selected_count": 2},
             list_views_cli.PROBE_STARTED_KEY: time.monotonic(), "worktrees": [
                 _row("atlas-core-wsl-20260101-host", machine="atlas-core", platform="wsl",
                      status="finalized"),
                 _row("atlas-core-wsl-20260101-live", machine="atlas-core", platform="wsl",
                      status="active"),
             ]},
            {"machine": "ember", "env": "linux", "reachable": True,
             "projection": {**_CURRENT, "roster_count": 2, "selected_count": 2},
             list_views_cli.PROBE_STARTED_KEY: time.monotonic(), "worktrees": [eligible, blocked]},
        ],
    )
    monkeypatch.setattr(delegate_cli.cfg, "project_name", lambda: "agent-worktrees")
    monkeypatch.setattr(delegate_cli.cfg, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        delegate_cli.list_views_cli,
        "_fleet_targets",
        lambda config: [("ember", "linux", "ember", "bash", False)],
    )
    seen = []

    def _fake_finalize(target, **kwargs):
        seen.append(target["id"])
        return {"worktree_id": target["id"], "ok": True, "success": True}

    monkeypatch.setattr(delegate_cli, "_finalize_target", _fake_finalize)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = delegate_cli.run_delegates(["--json", "--execute"])
    assert rc == 0
    payload = json.loads(buf.getvalue())

    assert seen == ["ember-linux-20260101-delegate"]
    assert [row["id"] for row in payload["eligible"]] == ["ember-linux-20260101-delegate"]
    assert [row["id"] for row in payload["blocked"]] == ["ember-linux-20260101-blocked"]
    assert payload["changed"] == [{"worktree_id": "ember-linux-20260101-delegate", "ok": True, "success": True}]


def test_finalize_target_parses_banner_noise(monkeypatch):
    monkeypatch.setattr(
        delegate_cli.list_views_cli,
        "_local_project_argv",
        lambda project: [project],
    )
    monkeypatch.setattr(
        delegate_cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Welcome\n{\"worktree_id\":\"wt\",\"success\":true}\n",
            stderr="",
        ),
    )

    result = delegate_cli._finalize_target(
        _row("wt", machine="atlas-core", platform="wsl", status="active"),
        project="agent-worktrees",
        timeout=5,
        host_index={("atlas-core", "wsl"): ("atlas-core-wsl", "bash", True)},
        source_key=("atlas-core", "wsl"),
    )

    assert result["ok"] is True
    assert result["worktree_id"] == "wt"


@pytest.mark.parametrize("local", [True, False])
def test_finalize_target_uses_shared_headless_flags_for_local_and_ssh(monkeypatch, local):
    calls = []
    monkeypatch.setattr(delegate_cli.list_views_cli, "_local_project_argv", lambda _: ["fixture"])
    monkeypatch.setattr(
        delegate_cli.subprocess, "run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(
            returncode=0, stdout='{"success":true}', stderr="",
        ),
    )
    result = delegate_cli._finalize_target(
        _row("wt", machine="host", platform="windows", status="active"),
        project="project", timeout=1,
        host_index={("host", "windows"): ("host", "pwsh", local)},
        source_key=("host", "windows"),
    )
    assert result["ok"] is True
    for name, value in delegate_cli.no_window_kwargs().items():
        assert calls[0][1][name] == value
