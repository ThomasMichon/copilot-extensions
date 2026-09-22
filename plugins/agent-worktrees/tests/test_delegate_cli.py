from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

from agent_worktrees import delegate_cli
from agent_worktrees import list_cli
from agent_worktrees import list_views_cli
from agent_worktrees import sessions, tracking


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
        "lambda-core-wsl-20260101-host",
        machine="lambda-core",
        platform="wsl",
        status="finalized",
    )
    delegate = _row(
        "wheatley-linux-20260101-delegate",
        machine="wheatley",
        platform="linux",
        status="active",
        caller_worktree="lambda-core-wsl-20260101-host",
    )

    delegate_cli.annotate_delegate_graph(
        [host, delegate],
        reachable_hosts={
            ("lambda-core", "wsl"): True,
            ("wheatley", "linux"): True,
        },
    )

    assert delegate["caller_state"]["state"] == "resolved"
    assert delegate["caller_state"]["status"] == "finalized"
    assert delegate["delegate_finalizable"] is True
    assert delegate["delegate_finalizable_reason"] == "host-finalized"
    assert host["delegates"] == [{
        "worktree_id": "wheatley-linux-20260101-delegate",
        "machine": "wheatley",
        "platform": "linux",
        "path": "/wt/wheatley-linux-20260101-delegate",
        "status": "active",
        "finalizable": True,
        "reason": "host-finalized",
    }]


def test_annotate_delegate_graph_keeps_active_host_blocking():
    host = _row(
        "lambda-core-wsl-20260101-host",
        machine="lambda-core",
        platform="wsl",
        status="active",
    )
    delegate = _row(
        "wheatley-linux-20260101-delegate",
        machine="wheatley",
        platform="linux",
        status="active",
        caller_worktree="lambda-core-wsl-20260101-host",
    )

    delegate_cli.annotate_delegate_graph([host, delegate])

    assert delegate["caller_state"]["state"] == "resolved"
    assert delegate["delegate_finalizable"] is False
    assert delegate["delegate_finalizable_reason"] == "host-active"


def test_annotate_delegate_graph_marks_missing_reachable_host_gone():
    delegate = _row(
        "wheatley-linux-20260101-delegate",
        machine="wheatley",
        platform="linux",
        status="active",
        caller_worktree="lambda-core-wsl-20260101-host",
    )

    delegate_cli.annotate_delegate_graph(
        [delegate],
        reachable_hosts={
            ("lambda-core", "wsl"): True,
            ("wheatley", "linux"): True,
        },
    )

    assert delegate["caller_state"] == {
        "state": "gone",
        "worktree_id": "lambda-core-wsl-20260101-host",
        "machine": "lambda-core",
        "platform": "wsl",
        "repo": "example",
    }
    assert delegate["delegate_finalizable"] is True
    assert delegate["delegate_finalizable_reason"] == "host-gone"


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
        "lambda-core-wsl-20260101-host",
        machine="lambda-core",
        platform="wsl",
        status="finalized",
    )
    delegate = _record(
        "wheatley-linux-20260101-delegate",
        machine="wheatley",
        platform="linux",
        status="active",
        caller_worktree="lambda-core-wsl-20260101-host",
    )
    monkeypatch.setattr(
        list_cli.sessions,
        "scan_sessions_fast",
        lambda records: sessions.SessionContext(),
    )

    payload = list_cli._build_list_json_payload(args, [host, delegate], stamp_session_state=False)
    by_id = {row["id"]: row for row in payload["worktrees"]}

    assert by_id["wheatley-linux-20260101-delegate"]["caller_state"]["state"] == "resolved"
    assert by_id["wheatley-linux-20260101-delegate"]["delegate_finalizable"] is True
    assert by_id["lambda-core-wsl-20260101-host"]["delegates"][0]["worktree_id"] == (
        "wheatley-linux-20260101-delegate"
    )


def test_run_fleet_json_annotates_delegate_graph(monkeypatch):
    hosts = [
        {
            "machine": "lambda-core",
            "env": "wsl",
            "reachable": True,
            "worktrees": [_row(
                "lambda-core-wsl-20260101-host",
                machine="lambda-core",
                platform="wsl",
                status="finalized",
            )],
        },
        {
            "machine": "wheatley",
            "env": "linux",
            "reachable": True,
            "worktrees": [_row(
                "wheatley-linux-20260101-delegate",
                machine="wheatley",
                platform="linux",
                status="active",
                caller_worktree="lambda-core-wsl-20260101-host",
            )],
        },
    ]
    monkeypatch.setattr(
        list_views_cli,
        "_fleet_targets",
        lambda config: [
            ("lambda-core", "wsl", "lambda-core-wsl", "bash", True),
            ("wheatley", "linux", "wheatley", "bash", False),
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
        lambda: SimpleNamespace(machine="lambda-core", platform="wsl", default_repo=SimpleNamespace(anchor=".")),
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


def test_run_delegates_execute_finalizes_only_eligible(monkeypatch):
    eligible = _row(
        "wheatley-linux-20260101-delegate",
        machine="wheatley",
        platform="linux",
        status="active",
        caller_worktree="lambda-core-wsl-20260101-host",
    )
    eligible["caller_state"] = {"state": "resolved", "worktree_id": "lambda-core-wsl-20260101-host"}
    eligible["delegate_finalizable"] = True
    eligible["delegate_finalizable_reason"] = "host-finalized"
    blocked = _row(
        "wheatley-linux-20260101-blocked",
        machine="wheatley",
        platform="linux",
        status="active",
        caller_worktree="lambda-core-wsl-20260101-live",
    )
    blocked["caller_state"] = {"state": "resolved", "worktree_id": "lambda-core-wsl-20260101-live"}
    blocked["delegate_finalizable"] = False
    blocked["delegate_finalizable_reason"] = "host-active"
    monkeypatch.setattr(
        delegate_cli,
        "_fleet_snapshot",
        lambda **kwargs: [{"machine": "wheatley", "env": "linux", "reachable": True, "worktrees": [eligible, blocked]}],
    )
    monkeypatch.setattr(delegate_cli.cfg, "project_name", lambda: "agent-worktrees")
    monkeypatch.setattr(delegate_cli.cfg, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        delegate_cli.list_views_cli,
        "_fleet_targets",
        lambda config: [("wheatley", "linux", "wheatley", "bash", False)],
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

    assert seen == ["wheatley-linux-20260101-delegate"]
    assert [row["id"] for row in payload["eligible"]] == ["wheatley-linux-20260101-delegate"]
    assert [row["id"] for row in payload["blocked"]] == ["wheatley-linux-20260101-blocked"]
    assert payload["changed"] == [{"worktree_id": "wheatley-linux-20260101-delegate", "ok": True, "success": True}]


def test_finalize_target_parses_banner_noise(monkeypatch):
    monkeypatch.setattr(
        delegate_cli.list_views_cli,
        "_local_binstub",
        lambda project: project,
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
        _row("wt", machine="lambda-core", platform="wsl", status="active"),
        project="agent-worktrees",
        timeout=5,
        host_index={("lambda-core", "wsl"): ("lambda-core-wsl", "bash", True)},
    )

    assert result["ok"] is True
    assert result["worktree_id"] == "wt"
