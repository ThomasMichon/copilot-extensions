"""Tracked effort-driver loop declaration and producer contracts."""

from __future__ import annotations

import json

from agent_dispatch.effort_driver_loops import (
    expand_effort_driver_loop,
    run_tick,
    validate_config,
)
from agent_dispatch.registrar_discovery import read_declaration_file_set


def _config(tmp_path, **overrides):
    config = {
        "name": "effort-driver",
        "kind": "effort-driver-loop",
        "repo": "example/project",
        "source": "effort-driver",
        "cadence_seconds": 3600,
        "tick_interval_seconds": 60,
        "state_root": str(tmp_path),
        "task_label": "effort-work",
        "pool": {
            "max_active_processes": 1,
            "body": {"type": "headless", "agent": "effort-worker"},
        },
    }
    config.update(overrides)
    return config


class FakeClient:
    def __init__(self, tasks=()):
        self.tasks = list(tasks)
        self.created = []
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return list(self.tasks)

    def create(self, title, **fields):
        task = {
            "id": f"task-{len(self.created) + 1}",
            "title": title,
            "status": "queued",
            **fields,
        }
        self.created.append(task)
        self.tasks.append(task)
        return task


def _write_effort(tmp_path, slug, *, title, status, body=""):
    path = tmp_path / "efforts" / "active" / slug / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                f"- **Status:** {status}",
                "",
                body,
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def test_expand_effort_driver_loop_builds_emitter_and_worker_lane(tmp_path):
    declarations = expand_effort_driver_loop(_config(tmp_path), repo_root=tmp_path)

    source = next(d for d in declarations if d.name == "effort-driver-source")
    workers = next(d for d in declarations if d.name == "effort-driver-workers")

    assert source.kind == "emitter"
    assert "effort_driver_loop" in source.spec
    assert workers.body.type == "headless"
    assert workers.body.agent == "effort-worker"


def test_global_effort_driver_discovers_active_effort_and_authors_goal_driven_task(
    tmp_path,
):
    _write_effort(
        tmp_path,
        "recipe-library",
        title="agent-dispatch recipe library",
        status="In Progress",
        body="Coordination: #4691 and #5200",
    )
    _write_effort(
        tmp_path,
        "other-work",
        title="something else",
        status="Draft",
    )
    path = tmp_path / "effort-driver.json"
    path.write_text(
        json.dumps(
            {
                "extends": "global:effort-driver",
                "name": "effort-driver",
                "repo": "example/project",
                "source": "effort-driver",
                "cadence_seconds": 3600,
                "tick_interval_seconds": 60,
                "state_root": str(tmp_path),
                "effort_slugs": ["recipe-library"],
                "task_label": "effort-work",
                "pool": {
                    "max_active_processes": 1,
                    "body": {"agent": "effort-worker"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path, repo_root=tmp_path)
    source = next(d for d in declarations if d.name == "effort-driver-source")
    config = source.spec["effort_driver_loop"]

    result = run_tick(FakeClient(), config, clock=lambda: 10_000, cwd=tmp_path)

    task = result["created"][0]
    payload = json.loads(task["payload_inline"])["effort_driver_loop"]
    assert task["require_verification"] is True
    assert task["evaluator_ref"] == "effort-driver"
    assert task["title"] == "Drive effort agent-dispatch recipe library to archive state"
    assert task["goal"] == "Drive tracked effort agent-dispatch recipe library to archive state"
    assert "Active effort README: efforts\\active\\recipe-library\\README.md" in task["prompt"]
    assert "Current recorded status: In Progress" in task["prompt"]
    assert "Coordination refs already noted: #4691, #5200" in task["prompt"]
    assert "execution half only" in task["prompt"]
    assert payload["effort_slug"] == "recipe-library"
    assert payload["effort_readme"] == "efforts\\active\\recipe-library\\README.md"
    assert payload["coordination_refs"] == ["#4691", "#5200"]


def test_explicit_effort_slugs_require_every_named_effort_to_exist(tmp_path):
    _write_effort(
        tmp_path,
        "recipe-library",
        title="agent-dispatch recipe library",
        status="In Progress",
    )
    config = validate_config(
        _config(tmp_path, effort_slugs=["recipe-library", "missing-effort"]),
        cwd=tmp_path,
    )

    result = run_tick(FakeClient(), config, clock=lambda: 10_000, cwd=tmp_path)

    assert result["created"] == []
    assert result["missing_effort_slugs"] == ["missing-effort"]
