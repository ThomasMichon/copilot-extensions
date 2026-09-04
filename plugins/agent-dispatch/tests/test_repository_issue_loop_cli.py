"""Operator surfaces for declarative repository issue loops."""

from __future__ import annotations

import json
import time

from agent_dispatch.__main__ import main
from agent_dispatch.repository_issue_loops import Issue


def _write_loop(path):
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "name": "backlog",
                "kind": "repository-issue-loop",
                "repo": "example/project",
                "source": "repository-backlog",
                "cadence_seconds": 3600,
                "tick_interval_seconds": 60,
                "quiet_period_seconds": 0,
                "include_labels": ["ready"],
                "exclude_labels": ["bootstrap"],
                "priority_labels": ["priority:high"],
                "batch_size": 1,
                "task_label": "repository-issue-work",
                "forge": {
                    "provider": "github",
                    "producer_login": "issue-bot",
                },
                "reservation": {
                    "label": "agent-reserved",
                    "comment": True,
                    "orphan_after_seconds": 600,
                },
                "pool": {
                    "max_active_processes": 1,
                    "body": {"type": "headless", "agent": "issue-worker"},
                },
            }
        ),
        encoding="utf-8",
    )


class FakeClient:
    def __init__(self, tasks=(), reservations=()):
        self.tasks = list(tasks)
        self.reservations = list(reservations)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def list(self, **_kwargs):
        return list(self.tasks)

    def list_reservations(self, *, task_id=None, state=None, **_kwargs):
        return [
            reservation
            for reservation in self.reservations
            if (task_id is None or reservation.get("task_id") == task_id)
            and (state is None or reservation.get("state") == state)
        ]


def test_inspect_disable_and_enable_cover_both_units(
    tmp_path, monkeypatch, capsys
):
    declaration = (
        tmp_path / "repo" / ".agent-dispatch" / "registrar" / "issues.json"
    )
    overrides = tmp_path / "overrides.json"
    _write_loop(declaration)
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(overrides))

    assert main(["repository-issue-loop", "inspect", str(declaration)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert [unit["kind"] for unit in inspected["units"]] == [
        "emitter",
        "supervised-lane",
    ]

    assert (
        main(
            [
                "repository-issue-loop",
                "disable",
                str(declaration),
                "--reason",
                "maintenance",
            ]
        )
        == 0
    )
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["enabled"] is False
    assert len(disabled["changed"]) >= 2

    assert main(["repository-issue-loop", "enable", str(declaration)]) == 0
    enabled = json.loads(capsys.readouterr().out)
    assert enabled["enabled"] is True
    assert json.loads(overrides.read_text(encoding="utf-8")) == {}


def test_discover_is_dry_run_and_reports_deterministic_issue(
    tmp_path, monkeypatch, capsys
):
    from agent_dispatch import __main__ as cli
    from agent_dispatch import repository_issue_loops as loops

    declaration = (
        tmp_path / "repo" / ".agent-dispatch" / "registrar" / "issues.json"
    )
    _write_loop(declaration)
    monkeypatch.setattr(cli, "_client", lambda _args: FakeClient())
    monkeypatch.setattr(
        loops.GitHubProvider,
        "list_open_issues",
        lambda _self, _repo: [
            Issue(
                42,
                "Fix race",
                "https://example.com/issues/42",
                ("ready", "priority:high"),
                1,
                1,
            )
        ],
    )

    assert main(["repository-issue-loop", "discover", str(declaration)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["eligible"] == [42]
    assert output["created"] == []
    assert output["reserved"] == []


def test_doctor_exposes_forge_failure_and_emitter_failure(
    tmp_path, monkeypatch, capsys
):
    from agent_dispatch import __main__ as cli
    from agent_dispatch import repository_issue_loops as loops
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope

    declaration = (
        tmp_path / "repo" / ".agent-dispatch" / "registrar" / "issues.json"
    )
    registrar_dir = tmp_path / "registrar-state"
    _write_loop(declaration)
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(registrar_dir))
    assert main(["repository-issue-loop", "setup", str(declaration)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "_client", lambda _args, **_kwargs: FakeClient())
    monkeypatch.setattr(
        "agent_dispatch.remote_dispatch.local_machine", lambda: "host-a"
    )
    monkeypatch.setattr("agent_dispatch.single_instance.is_locked", lambda _p: True)

    def fail_forge(_self, _repo):
        raise RuntimeError("credential unavailable")

    monkeypatch.setattr(loops.GitHubProvider, "list_open_issues", fail_forge)
    registrations = cli._repository_issue_loop_registrations(
        cli.build_parser().parse_args(
            ["repository-issue-loop", "status", str(declaration)]
        )
    )
    source = next(item for item in registrations if item["kind"] == "emitter")
    health = cli._repository_issue_loop_health_path(
        source["id"], "host-a", "default"
    )
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "ok": False,
                "error": "GitHub operation failed",
            }
        ),
        encoding="utf-8",
    )
    runtime = cli._supervisor_runtime_status_path(
        supervisor_lease_scope("host-a", "default")
    )
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "running": [item["id"] for item in registrations],
                "backing_off": [],
                "dead": [],
            }
        ),
        encoding="utf-8",
    )

    assert main(["repository-issue-loop", "doctor", str(declaration)]) == 1

    output = json.loads(capsys.readouterr().out)
    assert "forge-unavailable" in output["diagnoses"]
    assert "emitter-failure" in output["diagnoses"]
    assert output["forge_error"] == "credential unavailable"
    assert output["kill_switch"]["disabled"] is False


def test_doctor_exposes_exhausted_spawn_attempts(
    tmp_path, monkeypatch, capsys
):
    from agent_dispatch import __main__ as cli
    from agent_dispatch import repository_issue_loops as loops
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope

    declaration = (
        tmp_path / "repo" / ".agent-dispatch" / "registrar" / "issues.json"
    )
    registrar_dir = tmp_path / "registrar-state"
    _write_loop(declaration)
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(registrar_dir))
    assert main(["repository-issue-loop", "setup", str(declaration)]) == 0
    capsys.readouterr()
    task = {
        "id": "task-dead-lettered",
        "status": "queued",
        "origin_ref": "backlog/occurrence/1",
        "awaiting_steer": False,
    }
    reservations = [
        {
            "task_id": task["id"],
            "state": "failed",
            "attempt": attempt,
        }
        for attempt in range(1, 4)
    ]
    monkeypatch.setattr(
        cli,
        "_client",
        lambda _args, **_kwargs: FakeClient([task], reservations),
    )
    monkeypatch.setattr(
        "agent_dispatch.remote_dispatch.local_machine", lambda: "host-a"
    )
    monkeypatch.setattr("agent_dispatch.single_instance.is_locked", lambda _p: True)
    monkeypatch.setattr(
        loops.GitHubProvider,
        "list_open_issues",
        lambda _self, _repo: [],
    )
    registrations = cli._repository_issue_loop_registrations(
        cli.build_parser().parse_args(
            ["repository-issue-loop", "status", str(declaration)]
        )
    )
    runtime = cli._supervisor_runtime_status_path(
        supervisor_lease_scope("host-a", "default")
    )
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "running": [item["id"] for item in registrations],
                "backing_off": [],
                "dead": [],
            }
        ),
        encoding="utf-8",
    )

    assert main(["repository-issue-loop", "doctor", str(declaration)]) == 1

    output = json.loads(capsys.readouterr().out)
    assert output["healthy"] is False
    assert "spawn-dead-lettered" in output["diagnoses"]
    assert output["active_occurrence"]["spawn_failures"] == 3
    assert output["active_occurrence"]["spawn_attempt_limit"] == 3
    assert output["active_occurrence"]["spawn_dead_lettered"] is True
    assert output["active_occurrence"]["spawn_recovery"] is None
    assert output["actions"] == [
        "agent-dispatch reservations rearm task-dead-lettered "
        "--permit --reason <reason>"
    ]


def test_doctor_does_not_recommend_unsupported_rearm(
    tmp_path, monkeypatch, capsys
):
    from agent_dispatch import __main__ as cli
    from agent_dispatch import repository_issue_loops as loops
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope

    declaration = (
        tmp_path / "repo" / ".agent-dispatch" / "registrar" / "issues.json"
    )
    registrar_dir = tmp_path / "registrar-state"
    _write_loop(declaration)
    config = json.loads(declaration.read_text(encoding="utf-8"))
    config["pool"]["max_attempts"] = 2
    declaration.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(registrar_dir))
    assert main(["repository-issue-loop", "setup", str(declaration)]) == 0
    capsys.readouterr()
    task = {
        "id": "task-two-attempts",
        "status": "queued",
        "origin_ref": "backlog/occurrence/2",
        "awaiting_steer": False,
    }
    reservations = [
        {"task_id": task["id"], "state": "failed", "attempt": attempt}
        for attempt in range(1, 3)
    ]
    monkeypatch.setattr(
        cli,
        "_client",
        lambda _args, **_kwargs: FakeClient([task], reservations),
    )
    monkeypatch.setattr(
        "agent_dispatch.remote_dispatch.local_machine", lambda: "host-a"
    )
    monkeypatch.setattr("agent_dispatch.single_instance.is_locked", lambda _p: True)
    monkeypatch.setattr(
        loops.GitHubProvider,
        "list_open_issues",
        lambda _self, _repo: [],
    )
    registrations = cli._repository_issue_loop_registrations(
        cli.build_parser().parse_args(
            ["repository-issue-loop", "status", str(declaration)]
        )
    )
    runtime = cli._supervisor_runtime_status_path(
        supervisor_lease_scope("host-a", "default")
    )
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "running": [item["id"] for item in registrations],
                "backing_off": [],
                "dead": [],
            }
        ),
        encoding="utf-8",
    )

    assert main(["repository-issue-loop", "doctor", str(declaration)]) == 1

    output = json.loads(capsys.readouterr().out)
    assert "spawn-dead-lettered" in output["diagnoses"]
    assert output["actions"] == []
    assert "requires at least 3" in output["active_occurrence"]["spawn_recovery"]
