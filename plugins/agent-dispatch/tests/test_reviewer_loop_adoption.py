"""Reviewer-loop compatibility with existing trusted unit identities."""

from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_discovery import (
    Pointer,
    discover_trusted,
    read_declaration_file_set,
)
from agent_dispatch.registrar_reconcile import declaration_to_registration
from agent_dispatch.reviewer_loops import expand_reviewer_loop


@pytest.fixture
def reviewer_loop():
    return {
        "name": "project-review",
        "kind": "reviewer-loop",
        "repo": "github.com/example/project",
        "task_label": "external-review",
        "owner": "repo:project",
        "filters": {"permit": {"machine": ["host-a"]}},
        "emitter": {
            "command": ["reviews", "discover"],
            "interval_seconds": 60,
            "task_output": "json",
            "side_load": {"command": ["reviews", "side-load", "{change_ref}"]},
        },
        "evaluator": {
            "name": "existing-evaluator",
            "evaluator_ref": "existing:review-selector",
            "evaluator_spec": {"rules": []},
        },
        "pool": {
            "name": "existing-workers",
            "additional_labels": ["review-inbox"],
            "max_active_processes": 2,
            "body": {"type": "headless", "agent": "reviewer"},
        },
    }


def test_reviewer_loop_preserves_existing_identities_and_headless_default(reviewer_loop):
    source, evaluator, pool = expand_reviewer_loop(reviewer_loop)
    assert [source.name, evaluator.name, pool.name] == [
        "project-review-source",
        "existing-evaluator",
        "existing-workers",
    ]
    assert source.spec["id"] == "project-review-source"
    assert (
        source.spec["evaluator_ref"]
        == evaluator.spec["evaluator_ref"]
        == ("existing:review-selector")
    )
    assert "name" not in evaluator.spec
    assert evaluator.spec["repo"] == pool.repos == reviewer_loop["repo"]
    assert pool.labels == ("external-review", "review-inbox")
    assert pool.concurrency == 2
    assert pool.body.agent == "reviewer"
    assert all(
        unit.filters.permit == {"machine": frozenset({"host-a"})}
        for unit in (source, evaluator, pool)
    )
    registration = declaration_to_registration(pool, machine="host-a")
    assert registration["id"] == "declared:repo:project:existing-workers"
    assert registration["spec"]["headless_agent"] == "reviewer"
    assert "embody_backend" not in registration["spec"]
    assert "--embody-backend" not in pool.to_supervise_args()
    from agent_dispatch.spawn_factories import make_headless_spawn

    spawn = make_headless_spawn(agent=pool.body.agent)
    assert spawn.ordinary_bridge_for({}) is True
    # Expansion must not consume or normalize the caller's compatibility fields.
    assert reviewer_loop["pool"]["name"] == "existing-workers"
    assert reviewer_loop["evaluator"]["name"] == "existing-evaluator"


@pytest.mark.parametrize(
    ("section", "field", "default"),
    [
        ("pool", "name", "project-review-workers"),
        ("evaluator", "name", "project-review-evaluator"),
        ("evaluator", "evaluator_ref", "project-review-lifecycle"),
    ],
)
def test_reviewer_loop_identity_defaults_are_independent(reviewer_loop, section, field, default):
    del reviewer_loop[section][field]
    source, evaluator, pool = expand_reviewer_loop(reviewer_loop)
    if field == "evaluator_ref":
        assert source.spec[field] == evaluator.spec[field] == default
    else:
        assert (pool if section == "pool" else evaluator).name == default


def test_reviewer_loop_legacy_expansion_equals_explicit_generated_defaults(reviewer_loop):
    for section, field in (
        ("pool", "name"),
        ("evaluator", "name"),
        ("evaluator", "evaluator_ref"),
    ):
        del reviewer_loop[section][field]
    legacy = expand_reviewer_loop(reviewer_loop)
    reviewer_loop["pool"]["name"] = "project-review-workers"
    reviewer_loop["evaluator"].update(
        name="project-review-evaluator", evaluator_ref="project-review-lifecycle"
    )
    assert expand_reviewer_loop(reviewer_loop) == legacy


def test_reviewer_loop_yaml_preserves_existing_identities(reviewer_loop, tmp_path):
    import yaml

    path = tmp_path / "review.yaml"
    path.write_text(yaml.safe_dump(reviewer_loop), encoding="utf-8")
    assert read_declaration_file_set(path) == expand_reviewer_loop(reviewer_loop)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("pool", "name", None),
        ("pool", "name", ""),
        ("pool", "name", 7),
        ("pool", "name", "bad/name"),
        ("evaluator", "name", False),
        ("evaluator", "name", ""),
        ("evaluator", "name", "bad name"),
        ("evaluator", "evaluator_ref", None),
        ("evaluator", "evaluator_ref", ""),
        ("evaluator", "evaluator_ref", " \t"),
        ("evaluator", "evaluator_ref", 7),
        ("evaluator", "evaluator_ref", []),
        ("emitter", "evaluator_ref", "conflicting-selector"),
    ],
)
def test_reviewer_loop_rejects_invalid_identity_overrides(reviewer_loop, section, field, value):
    reviewer_loop[section][field] = value
    with pytest.raises(RegistrarError, match=f"{field}|derived from the loop"):
        expand_reviewer_loop(reviewer_loop)


@pytest.mark.parametrize(
    ("section", "name"),
    [
        ("pool", "project-review-source"),
        ("evaluator", "project-review-source"),
        ("pool", "existing-evaluator"),
        ("evaluator", "existing-workers"),
    ],
)
def test_reviewer_loop_rejects_colliding_unit_names(reviewer_loop, section, name):
    reviewer_loop[section]["name"] = name
    with pytest.raises(RegistrarError, match="duplicate profile name"):
        expand_reviewer_loop(reviewer_loop)


def test_reviewer_loop_registry_rejects_conflicting_existing_unit(reviewer_loop, tmp_path):
    (tmp_path / "review.json").write_text(json.dumps(reviewer_loop), encoding="utf-8")
    (tmp_path / "conflict.json").write_text(
        json.dumps({"name": "existing-workers", "labels": ["other-work"]}),
        encoding="utf-8",
    )
    with pytest.raises(RegistrarError, match="duplicate profile name 'existing-workers'"):
        discover_trusted([Pointer("project", str(tmp_path), owner="repo:project")])


class _Client:
    def __init__(self):
        self.list_calls = []

    def list_registrations(self, **_scope):
        return []

    def list(self, **scope):
        self.list_calls.append(scope)
        return []


@pytest.mark.parametrize("command", ["inspect", "status", "doctor", "side-load"])
def test_reviewer_loop_commands_share_adopted_units(
    reviewer_loop, command, tmp_path, monkeypatch, capsys
):
    from agent_dispatch import __main__ as cli
    from agent_dispatch import reviewer_loop_commands as commands
    from agent_dispatch.producers import emitter

    declaration = tmp_path / "review.json"
    declaration.write_text(json.dumps(reviewer_loop), encoding="utf-8")
    client = _Client()
    monkeypatch.setattr(cli, "_client", lambda *_args, **_kwargs: nullcontext(client))
    monkeypatch.setattr("agent_dispatch.remote_dispatch.local_machine", lambda: "host-a")
    monkeypatch.setattr("agent_dispatch.registrar_discovery.load_pointers", lambda: [])
    monkeypatch.setattr("agent_dispatch.single_instance.is_locked", lambda _path: False)
    monkeypatch.setattr(commands, "_read_supervisor_runtime_status", lambda _scope: (None, None))
    monkeypatch.setattr("agent_dispatch.overrides.load_overrides", lambda _path: {})
    observed = []

    def side_load(_client, registration, change_ref, **scope):
        observed.append(registration)
        assert change_ref == "example/project#42"
        assert scope == {"current_machine": "host-a", "current_env": "default"}
        return {"created": []}

    monkeypatch.setattr(emitter, "run_side_load", side_load)
    argv = ["reviewer-loop", command, str(declaration)]
    if command == "side-load":
        argv.append("example/project#42")
    assert cli.main(argv) == (1 if command == "doctor" else 0)
    output = json.loads(capsys.readouterr().out)
    registrations = [
        declaration_to_registration(unit, machine="host-a")
        for unit in read_declaration_file_set(declaration)
    ]
    if command == "side-load":
        assert observed == [registrations[0]]
    else:
        assert [unit["logical_id"] for unit in output["units"]] == [
            unit["logical_id"] for unit in registrations
        ]
        assert [unit["spec"] for unit in output["units"]] == [
            unit["spec"] for unit in registrations
        ]
        if command != "inspect":
            assert client.list_calls[0]["evaluator_ref"] == "existing:review-selector"


def test_reviewer_loop_emitted_task_uses_selected_ref_without_changing_identity(reviewer_loop):
    from agent_dispatch.producers.emitter import _author_tasks

    source, evaluator, _pool = expand_reviewer_loop(reviewer_loop)
    created = []

    class Client:
        def create(self, title, **fields):
            created.append({"title": title, **fields})
            return {"id": "review-task"}

    task = {
        "title": "Review change",
        "repo": reviewer_loop["repo"],
        "labels": ["external-review"],
        "dedup_key": "review:example/project#42",
        "exclusive_key": "review:example/project#42",
        "evaluator_ref": "untrusted-output",
    }
    assert _author_tasks(Client(), source.spec, json.dumps(task)) == [{"id": "review-task"}]
    assert created == [
        {
            **task,
            "evaluator_ref": evaluator.spec["evaluator_ref"],
            "source": "emitter",
            "origin_ref": "project-review-source",
            "require_verification": False,
        }
    ]
