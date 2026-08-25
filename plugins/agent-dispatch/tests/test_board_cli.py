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


def test_remote_machine_falls_back_to_full_cli(monkeypatch):
    monkeypatch.setattr(board_cli, "_local_machine", lambda: "m1")
    captured = {}

    def run(command, check):
        captured["command"] = command
        assert check is False
        return types.SimpleNamespace(returncode=7)

    monkeypatch.setattr(board_cli.subprocess, "run", run)
    assert board_cli.main(["--machine", "m2"]) == 7
    assert captured["command"][:5] == [
        "agent-dispatch",
        "inbox",
        "--machine",
        "m2",
        "--board",
    ]
