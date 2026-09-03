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
    def __init__(self, tasks=()):
        self.tasks = list(tasks)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def list(self, **_kwargs):
        return list(self.tasks)


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
