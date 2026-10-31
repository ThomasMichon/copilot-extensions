"""Workers discovery does not withhold known templates or conceal partial failure."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from io import StringIO

import pytest

from agent_dispatch import workers_catalog
from agent_dispatch import early_cli
from agent_dispatch.registrar import RegistrarError


def arguments(*argv):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="workers_command")
    workers_catalog.register_commands(commands)
    return parser.parse_args(["catalog", *argv])


def test_plain_catalog_keeps_its_existing_json_contract(monkeypatch, capsys):
    payload = {"schema_version": 1, "entries": [{"id": "configured"}], "summary": {"configured": 1}}
    monkeypatch.setattr(workers_catalog, "catalog", lambda: payload)
    assert workers_catalog.command(arguments()) == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_stream_keeps_friendly_template_labels_and_stable_names(monkeypatch, capsys):
    rows = workers_catalog.builtin_template_rows()
    monkeypatch.setattr(workers_catalog, "catalog", lambda: {"entries": rows, "summary": {}})
    assert workers_catalog.command(arguments("--stream")) == 0
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    emitted = [frame["entry"] for frame in frames if frame["type"] == "row"]
    assert emitted == rows + rows
    pool = next(row for row in emitted if row["id"] == "template:worker-pool")
    assert pool["name"] == "worker-pool" and pool["title"] == "Task worker pool"
    assert pool["description"] == "Run eligible queued tasks with a bounded number of workers."


@pytest.mark.parametrize("failed", [False, True])
def test_stream_flushes_safe_templates_before_discovery_and_reports_completion(monkeypatch, failed):
    output = StringIO()
    template = {"id": "template:example", "activity": "not-applicable"}
    monkeypatch.setattr(workers_catalog, "builtin_template_rows", lambda: [template])
    monkeypatch.setattr("sys.stdout", output)

    def discover():
        first = [json.loads(line) for line in output.getvalue().splitlines()]
        assert [frame["type"] for frame in first] == ["begin", "summary", "row"]
        assert first[2]["entry"] == template
        assert "Loading configured workers" in first[1]["summary"]["banner_text"]
        if failed:
            raise RegistrarError("fixture discovery unavailable")
        return {
            "entries": [template, {"id": "configured", "activity": "unknown"}],
            "summary": {"configured": 1, "activity_known": False},
        }

    monkeypatch.setattr(workers_catalog, "catalog", discover)
    assert workers_catalog.command(arguments("--stream")) == (2 if failed else 0)
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    if failed:
        assert frames[-1] == {"type": "error", "message": "Workers discovery failed: fixture discovery unavailable"}
        assert not any(frame["type"] == "done" for frame in frames)
    else:
        assert frames[-2:] == [
            {"type": "summary", "summary": {"configured": 1, "activity_known": False}},
            {"type": "done"},
        ]
        rows = {frame["entry"]["id"]: frame["entry"] for frame in frames if frame["type"] == "row"}
        assert set(rows) == {"template:example", "configured"}
        assert rows["configured"]["activity"] == "unknown"


@pytest.mark.parametrize("argv", [
    ["workers", "catalog", "--stream"],
    ["workers", "save", "template:worker-pool", "--revision", "revision", "--field", "name=fixture"],
])
def test_early_local_route_reuses_authoritative_worker_parser(monkeypatch, argv):
    for key in tuple(os.environ):
        if key.startswith("AGENT_DISPATCH_TASK_"):
            monkeypatch.delenv(key)
    seen = []
    monkeypatch.setattr(workers_catalog, "command", lambda args: seen.append(args) or 7)
    assert early_cli.dispatch(argv) == 7
    assert seen[0].command == "workers" and seen[0].workers_command == argv[1]
    if argv[1] == "catalog":
        assert seen[0].stream is True
    else:
        assert seen[0].id == "template:worker-pool"
        assert seen[0].revision == "revision" and seen[0].field == ["name=fixture"]


@pytest.mark.parametrize("key", [
    "AGENT_DISPATCH_TASK_TOKEN", "AGENT_DISPATCH_TASK_ID", "AGENT_DISPATCH_TASK_URL",
    "AGENT_DISPATCH_TASK_WORKER_ID", "AGENT_DISPATCH_TASK_TOKEN_FILE",
])
def test_projected_task_authority_retains_full_cli_admission(monkeypatch, key):
    monkeypatch.setenv(key, "fixture")
    assert early_cli.dispatch(["workers", "save"]) is None


@pytest.mark.parametrize("argv", [
    ["workers", "pool-options"], ["workers", "config-section"],
    ["--local", "workers", "catalog"], ["status"],
])
def test_other_grammars_retain_the_existing_entrypoint(argv):
    assert early_cli.dispatch(argv) is None


@pytest.mark.parametrize("result", [0, 4])
def test_early_callback_retains_its_existing_owner(monkeypatch, result):
    from agent_dispatch import delegated_waiter_callback
    from agent_dispatch.delegated_waiter_contract import CALLBACK_COMMAND

    calls = []
    monkeypatch.setattr(
        delegated_waiter_callback, "main", lambda argv: calls.append(argv) or result,
    )
    assert early_cli.dispatch([CALLBACK_COMMAND, "--fixture"]) == result
    assert calls == [["--fixture"]]


def test_native_worker_entrypoint_does_not_import_unrelated_cli_families():
    code = """
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            "agent_dispatch.attention_cli",
            "agent_dispatch.task_lifecycle_registration_cli",
            "agent_dispatch.acquisition_receipts",
        }:
            raise AssertionError("unrelated CLI family imported: " + fullname)
sys.meta_path.insert(0, Guard())
sys.argv = ["agent-dispatch", "workers", "save", "--help"]
runpy.run_module("agent_dispatch", run_name="__main__", alter_sys=True)
"""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("AGENT_DISPATCH_TASK_")
    }
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=40 if os.name == "nt" else 20,
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert "--revision" in result.stdout and "--field" in result.stdout


def test_native_worker_observation_reads_receipt_without_supervisor_cli(tmp_path):
    code = """
import importlib.abc, json, os, sys, time
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            "agent_dispatch.supervise_cli",
            "agent_dispatch.loop_commands",
            "agent_dispatch.__main__",
        }:
            raise AssertionError("unrelated CLI family imported: " + fullname)
sys.meta_path.insert(0, Guard())
from agent_dispatch.supervisor_status_io import runtime_status_path
from agent_dispatch.supervisor_registration import supervisor_lease_scope
from agent_dispatch.workers_catalog import _observation
scope = supervisor_lease_scope("fixture-host", "default")
path = runtime_status_path(scope)
assert not path.exists()
assert _observation() == {"known": False, "reason": "no local supervisor receipt"}
assert not path.parent.exists()
path.parent.mkdir(parents=True)
path.write_text("[]", encoding="utf-8")
assert _observation() == {"known": False, "reason": "status-object-required"}
receipt = {"updated_at": time.time() - 1000, "running": ["fixture"]}
path.write_text(json.dumps(receipt), encoding="utf-8")
assert _observation() == {"known": False, "reason": "stale local supervisor receipt"}
receipt["updated_at"] = time.time()
path.write_text(json.dumps(receipt), encoding="utf-8")
assert _observation() == {"known": True, **receipt}
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=20,
        env={
            **os.environ, "AGENT_DISPATCH_RUN_DIR": str(tmp_path / "run"),
            "AGENT_DISPATCH_SUPERVISE_MACHINE": "fixture-host",
            "AGENT_DISPATCH_ENV": "default",
        },
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
