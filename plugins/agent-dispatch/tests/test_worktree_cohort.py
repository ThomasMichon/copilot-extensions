"""Original allocation evidence crosses only a capable creation boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_dispatch import embody, worktree_cohort


def cohort(task="original", key="dispatch-task:original:1"):
    return {
        "schema": "dispatch-assignment-cohort", "version": 1,
        "emitter": {
            "repo": "example", "source": "emitter", "request_id": "original-request",
            "producer_id": "source-reader", "task_id": task, "generation": 1,
        },
        "pool": {
            "task_id": task, "registration_id": "original-pool", "machine": "worker",
            "env": "linux", "reservation_key": key, "task_generation": 1,
            "catalog_revision": 7,
        },
    }


@pytest.fixture(autouse=True)
def clear_capabilities():
    worktree_cohort._supports_cohort.cache_clear()
    yield
    worktree_cohort._supports_cohort.cache_clear()


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """A strict current/prior process boundary, never the installed owner."""
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__main__.py").write_text(
        '''import argparse, json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["CALL_LOG"]).open("a") as out:
    out.write(json.dumps(args) + "\\n")
cap = json.loads(os.environ["CAPABILITIES"])
if args == ["worktree-allocation", "capabilities", "--json"]:
    if cap == "legacy":
        print("unknown command: worktree-allocation", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(cap))
    sys.exit(0)
parser = argparse.ArgumentParser()
parser.add_argument("--project")
parser.add_argument("operation", choices=["create"])
parser.add_argument("--no-owner", action="store_true")
parser.add_argument("--json", action="store_true")
parser.add_argument("--no-pair", action="store_true")
for flag in ("origin", "interface", "dispatch-task-id", "dispatch-reservation-key",
             "dispatch-attempt", "dispatch-driver", "dispatch-supervisor", "creation-key"):
    parser.add_argument("--" + flag)
if isinstance(cap, dict) and cap.get("dispatch_cohort"):
    parser.add_argument("--dispatch-cohort", type=json.loads)
parsed = parser.parse_args()
if os.environ.get("FAIL_CREATE"):
    print("error: unknown argument after an indeterminate creation", file=sys.stderr)
    sys.exit(2)
print(json.dumps({
    "creation": {"version": 1, "status": "complete"},
    "worktree": {"id": "workspace", "repo": parsed.project, "path": "/fixture/workspace"},
}))
''',
        encoding="utf-8",
    )
    log = tmp_path / "calls.jsonl"
    environment = {**os.environ, "PYTHONPATH": str(tmp_path), "CALL_LOG": str(log)}
    prefix = [sys.executable, "-m", "agent_worktrees"]
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: prefix)
    monkeypatch.setattr(embody, "agent_worktrees_environment", lambda: environment.copy())
    monkeypatch.setattr(worktree_cohort, "agent_worktrees_environment", lambda: environment.copy())
    capabilities = {
        "schema": "copilot-extensions.worktree-allocation", "version": 1, "role": "origin",
        "dispatch_cohort": {"schema": "dispatch-assignment-cohort", "version": 1},
    }
    environment["CAPABILITIES"] = json.dumps(capabilities)

    def create(**kwargs):
        return embody.create_worktree(
            project="example", task_id="original", reservation_key="dispatch-task:original:1",
            attempt=1, driver="agent-dispatch", supervisor="supervisor",
            creation_key="original-creation", allocation_cohort=kwargs.pop("allocation_cohort", cohort()),
            **kwargs,
        )

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return create, calls, environment, prefix, capabilities


@pytest.mark.parametrize("support", ["current", "prior", "legacy", "bad-schema", "future", "bool", "receiver"])
def test_real_parser_selects_capable_or_baseline_before_creation(provider, support, caplog):
    create, calls, env, _, cap = provider
    if support == "prior":
        cap.pop("dispatch_cohort")
    elif support == "legacy":
        cap = "legacy"
    elif support == "bad-schema":
        cap["schema"] = "unrelated"
    elif support == "future":
        cap["dispatch_cohort"]["version"] = 2
    elif support == "bool":
        cap["dispatch_cohort"]["version"] = True
    elif support == "receiver":
        cap["role"] = "receiver"
    env["CAPABILITIES"] = json.dumps(cap)
    assert create()["worktree"] == "workspace"
    discovery, creation = calls()
    assert discovery == ["worktree-allocation", "capabilities", "--json"]
    assert ("--dispatch-cohort" in creation) is (support == "current")
    if support == "current":
        assert json.loads(creation[creation.index("--dispatch-cohort") + 1]) == cohort()
    else:
        assert "legacy unattributed baseline" in caplog.text


def test_cached_selected_runtime_and_provider_rollback(provider):
    create, calls, env, prefix, cap = provider
    create()
    create()
    assert len(calls()) == 3  # One observation, two creations.
    prefix.insert(1, "-B")
    cap.pop("dispatch_cohort")
    env["CAPABILITIES"] = json.dumps(cap)
    create()
    assert len(calls()) == 5
    assert "--dispatch-cohort" not in calls()[-1]
    assert "--creation-key" in calls()[-1]


def test_creation_failure_is_not_an_effectful_syntax_probe(provider):
    create, calls, env, _, _ = provider
    env["FAIL_CREATE"] = "1"
    with pytest.raises(embody.EmbodyUnavailable, match="indeterminate creation"):
        create()
    assert len(calls()) == 2
    assert "--dispatch-cohort" in calls()[-1]


def test_legacy_reservation_has_no_discovery_or_guessed_cohort(provider):
    create, calls, _, _, _ = provider
    create(allocation_cohort=None)
    assert len(calls()) == 1
    assert "--dispatch-cohort" not in calls()[0]


@pytest.mark.parametrize("failure", [OSError("unavailable"), subprocess.TimeoutExpired("capabilities", 5)])
def test_unavailable_discovery_preserves_optional_baseline(monkeypatch, failure, caplog):
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(worktree_cohort.subprocess, "run", fail)
    assert worktree_cohort.creation_arguments(
        [sys.executable, "-m", "agent_worktrees"], cohort(),
    ) == []
    assert "support is unknown" in caplog.text


def test_mutable_shim_never_authorizes_new_syntax(monkeypatch):
    monkeypatch.setattr(worktree_cohort.subprocess, "run", lambda *_a, **_k: pytest.fail("no shim probe"))
    assert worktree_cohort.creation_arguments(["/fixture/bin/worktrees"], cohort()) == []


def test_new_creation_forwards_original_snapshot_not_current_task(monkeypatch):
    requests = []
    original = cohort()
    monkeypatch.setattr(embody, "create_worktree", lambda **kwargs:
        requests.append(kwargs) or {"worktree": "workspace", "path": "/fixture/workspace", "project": "example"})
    result = embody.prepare_reusable_worktree(
        {"id": "new-episode", "source": "replacement", "cohort": cohort("new-episode")},
        {
            "key": "dispatch-task:new-episode:3", "attempt": 3,
            "allocation_reservation_key": "dispatch-task:original:1",
            "allocation_project": "example", "worktree_path": None,
            "allocation_cohort": original, "creation_key": "original-creation",
        },
        interface="cli", driver="agent-dispatch", supervisor="supervisor",
    )
    assert result["created"] is True
    assert requests[0]["allocation_cohort"] == original
    assert requests[0]["task_id"] == "original"
    assert requests[0]["reservation_key"] == "dispatch-task:original:1"
    assert requests[0]["attempt"] == 1


@pytest.mark.parametrize("ownership", ["targeted", "reused"])
def test_existing_workspace_is_never_restamped(monkeypatch, ownership):
    monkeypatch.setattr(embody, "resolve_worktree", lambda *_a, **_k:
        {"worktree": "workspace", "path": "/fixture/workspace", "project": "example"})
    monkeypatch.setattr(embody, "create_worktree", lambda **_k: pytest.fail("no restamping"))
    monkeypatch.setattr(worktree_cohort, "creation_arguments", lambda *_a: pytest.fail("no discovery"))
    result = embody.prepare_reusable_worktree(
        {"id": "current"}, {
            "key": "dispatch-task:current:1", "attempt": 1, "worktree": "workspace",
            "worktree_ownership": ownership, "allocation_project": "example",
            "worktree_path": "/fixture/workspace", "allocation_cohort": cohort("current"),
        },
        interface="cli", driver="agent-dispatch", supervisor="supervisor",
    )
    assert result["created"] is False
