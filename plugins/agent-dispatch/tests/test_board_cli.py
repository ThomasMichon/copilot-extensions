from __future__ import annotations

import json
import types

from agent_dispatch import board_cli


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


def test_build_wt_badge_reflects_has_worktree(monkeypatch):
    """Operator feedback 2026-09-20 (item 2): a badge, not just the WT
    column's raw contents, makes a worktree-bearing Started task
    unmistakable at a glance."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [
            {"id": "with-wt", "status": "started",
             "target_worktree": "some-worktree"},
            {"id": "without-wt", "status": "started"},
        ],
        machine="m1",
        recent_mins=120,
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id["with-wt"]["wt_badge"] == "WT"
    assert by_id["without-wt"]["wt_badge"] is None


def test_build_artifacts_summary_is_a_phase3_placeholder(monkeypatch):
    """Phase 3 lands the column plumbing only; Phase 5 owns the real
    claims/artifacts computation (see the Plan's own note against the two
    phases both claiming this field)."""
    monkeypatch.setattr(board_cli.time, "time", lambda: 1000.0)
    rows = board_cli._build(
        [{"id": "t1", "status": "queued"}], machine="m1", recent_mins=120
    )
    assert rows[0]["artifacts_summary"] is None


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

    captured = {}

    def open_request(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(board_cli.urllib.request, "urlopen", open_request)
    assert board_cli.main(["--machine", "m1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "t1"
    assert captured["url"].startswith("http://127.0.0.1:1234/tasks?")
    assert captured["timeout"] == 3


def test_endpoint_maps_wildcard_bind_to_loopback(monkeypatch, tmp_path):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "0.0.0.0", "port": "4321"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path))
    assert board_cli._endpoint() == "http://127.0.0.1:4321"


def test_local_machine_reads_persisted_alias_before_hostname(monkeypatch, tmp_path):
    (tmp_path / "machine").write_text("augloop1", encoding="utf-8")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(board_cli.platform, "node", lambda: "CPC-tmich-OIXUI")
    assert board_cli._local_machine() == "augloop1"


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
