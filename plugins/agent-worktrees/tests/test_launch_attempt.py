"""Resolution receipts preserve correlation without becoming replay authority."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import launch_attempt as attempts, output, resolve_cli, tracking_write

pytestmark = pytest.mark.guard


@pytest.fixture
def receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    from agent_worktrees import launch_seed_state

    dispatch = launch_seed_state._dispatch
    monkeypatch.setattr(
        launch_seed_state, "_dispatch",
        lambda verb, args, **kw: attempts._apply(args) if verb == "launch_attempt_resolution"
        else dispatch(verb, args, **kw),
    )
    return tmp_path


def _run(attempt_id, plan, *, result=0):
    def work():
        output._json_output(plan)
        return result
    with output.capture_json_output() as captured:
        code = attempts.resolve(argparse.Namespace(launch_attempt_id=attempt_id), work)
    return code, json.loads(captured.getvalue())


@pytest.mark.parametrize("plan,phase,details", [
    ({"launch": {"action": "exec", "worktree_id": "wt-a", "seed_id": "seed-a"}},
     "plan-ready", {"worktree_id": "wt-a", "seed_id": "seed-a"}),
    ({"launch": {"action": "exec"}, "worktree": {"id": "allocated-a"}},
     "plan-ready", {"worktree_id": "allocated-a"}),
    ({"action": "exec", "worktree_id": None}, "plan-ready", {}),
    ({"action": "remote", "remote_command": "fixture"}, "forwarded", {}),
    ({"action": "remote", "worktree_id": "wt-r", "seed_id": "s", "launch_request_id": "r"},
     "remote-admission-returned", {"worktree_id": "wt-r", "seed_id": "s", "request_id": "r"}),
    ({"error": "fixture refusal"}, "unknown", {}),
])
def test_resolution_binds_only_allowlisted_returned_evidence(receipts, plan, phase, details):
    attempt_id = uuid4().hex
    code, returned = _run(attempt_id, plan, result=3 if "error" in plan else 0)
    assert code == (3 if "error" in plan else 0)
    launch = returned.get("launch", returned)
    receipt = launch["launch_attempt_receipt"]
    assert launch["launch_attempt_id"] == attempt_id
    assert receipt["phase"] == phase
    assert {key: value for key, value in receipt.items() if key not in {
        "phase", "version", "operation_id",
    }} == details
    saved = attempts.read(receipts, attempt_id, receipt["operation_id"])
    assert saved["events"][0]["phase"] == "resolution-requested"
    assert saved["events"][-1]["details"] == {"exit_code": code, **details}
    assert saved["events"][-1]["phase"] == phase
    assert "remote_command" not in json.dumps(saved)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("posix", [False, True])
def test_native_setup_owner_gets_exact_resolution_correlation_without_outer_waiter(receipts, wrapped, posix):
    attempt_id = uuid4().hex
    command = ([
        "bash", str(receipts / "default-setup.sh"),
        "--launch-seed-preparation", "admission", "--launch-seed-native-root", str(receipts / "engine"),
        "--", "literal",
    ] if posix else [
        "pwsh.exe", "-NoProfile", "-File", str(receipts / "default-setup.ps1"),
        "-LaunchSeedPreparation", "admission", "-LaunchSeedNativeRoot", str(receipts / "engine"),
        "-NativeArgumentsJson", json.dumps({"args": ["--", "literal"]}),
    ])
    if wrapped:
        command = ([
            "bash", str(receipts / "launch-command.sh"), "--", *command,
        ] if posix else [
            "pwsh.exe", "-NoProfile", "-File", str(receipts / "launch-command.ps1"), "--", *command,
        ])
    code, returned = _run(attempt_id, {"action": "exec", "cmd": command})
    assert code == 0
    result = returned["cmd"]
    start = ((5 if wrapped else 2) if posix else (9 if wrapped else 4))
    assert result[:start] == command[:start] and result[-2:] == command[-2:]
    assert result[start:start + 4] == [
        "--launch-attempt-id" if posix else "-LaunchAttemptId", attempt_id,
        "--launch-resolution-id" if posix else "-LaunchResolutionId",
        returned["launch_attempt_receipt"]["operation_id"],
    ]
    assert "agent_worktrees.launch_seed_exec" not in result


def test_initial_receipt_is_required_before_effects(receipts, monkeypatch):
    monkeypatch.setattr(attempts, "_append", lambda *a, **kw: (_ for _ in ()).throw(OSError("private")))
    with output.capture_json_output() as captured:
        assert attempts.resolve(
            argparse.Namespace(launch_attempt_id=uuid4().hex), lambda: pytest.fail("effect"),
        ) == 3
    assert "no resolution started" in captured.getvalue()
    assert "private" not in captured.getvalue()


@pytest.mark.parametrize("failure", [OSError("private fixture"), KeyboardInterrupt()])
def test_interrupted_resolution_retains_unknown_without_retry(receipts, failure, capsys):
    attempt_id = uuid4().hex
    calls = []
    def work():
        calls.append("allocation")
        raise failure
    with output.capture_json_output() as captured:
        assert attempts.resolve(argparse.Namespace(launch_attempt_id=attempt_id), work) == 3
    files = list((receipts / "launch-attempts" / attempt_id).glob("*.json"))
    saved = attempts.read(receipts, attempt_id, files[0].stem)
    assert saved["events"][-1]["phase"] == "unknown"
    assert calls == ["allocation"]
    assert "private fixture" not in captured.getvalue() + capsys.readouterr().err + json.dumps(saved)


def test_lost_terminal_write_never_reexecutes_or_publishes_a_launch_plan(receipts, monkeypatch):
    append = attempts._append
    calls = []
    def fail_terminal(*args, **kwargs):
        if args[3] != "resolution-requested":
            raise tracking_write.AmbiguousWriteOutcome("private fixture")
        return append(*args, **kwargs)
    monkeypatch.setattr(attempts, "_append", fail_terminal)
    def work():
        calls.append(True)
        output._json_output({"action": "exec", "worktree_id": "already-allocated"})
        return 0
    attempt_id = uuid4().hex
    with output.capture_json_output() as captured:
        assert attempts.resolve(argparse.Namespace(launch_attempt_id=attempt_id), work) == 3
    assert calls == [True]
    assert "cmd" not in json.loads(captured.getvalue())
    path = next((receipts / "launch-attempts" / attempt_id).glob("*.json"))
    assert len(attempts.read(receipts, attempt_id, path.stem)["events"]) == 1


def test_same_attempt_has_distinct_resolution_hops_not_an_allocation_retry_key(receipts):
    attempt_id = uuid4().hex
    operations = []
    for wid in ("first-allocation", "second-explicit-allocation"):
        code, plan = _run(attempt_id, {"action": "exec", "worktree_id": wid})
        assert code == 0
        operations.append(plan["launch_attempt_receipt"]["operation_id"])
    assert operations[0] != operations[1]
    assert len(list((receipts / "launch-attempts" / attempt_id).glob("*.json"))) == 2


def test_receipts_are_written_before_entering_resolution(receipts):
    attempt_id = uuid4().hex
    def work():
        path = next((receipts / "launch-attempts" / attempt_id).glob("*.json"))
        assert attempts.read(receipts, attempt_id, path.stem)["events"][0]["phase"] == "resolution-requested"
        output._json_output({"action": "exec", "worktree_id": "wt-a"})
        return 0
    with output.capture_json_output():
        assert attempts.resolve(argparse.Namespace(launch_attempt_id=attempt_id), work) == 0


def test_concurrent_transactions_do_not_merge_distinct_hops(receipts):
    attempt_id = uuid4().hex
    operations = [uuid4().hex for _ in range(12)]
    def write(operation_id):
        return attempts._apply({
            "tracking_dir": str(receipts), "attempt_id": attempt_id, "operation_id": operation_id,
            "phase": "resolution-requested", "details": {},
        })
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(write, operations)) == [{"recorded": True}] * len(operations)
    for operation_id in operations:
        assert len(attempts.read(receipts, attempt_id, operation_id)["events"]) == 1
        assert write(operation_id).get("error")


@pytest.mark.parametrize("damage", [
    {"schema": "foreign"}, {"version": True}, {"operation_id": "c" * 32}, {"events": []},
])
def test_foreign_or_corrupt_state_is_not_repaired(receipts, damage):
    attempt_id, operation_id = uuid4().hex, uuid4().hex
    args = {
        "tracking_dir": str(receipts), "attempt_id": attempt_id, "operation_id": operation_id,
        "phase": "resolution-requested", "details": {},
    }
    assert attempts._apply(args) == {"recorded": True}
    path = attempts._path(receipts, attempt_id, operation_id)
    value = {**json.loads(path.read_text(encoding="utf-8")), **damage}
    path.write_text(json.dumps(value), encoding="utf-8")
    before = path.read_bytes()
    assert attempts._apply({**args, "phase": "plan-ready"}).get("error")
    assert path.read_bytes() == before


@pytest.mark.parametrize("details", [{"text": "private"}, {"prompt": "private"}, {"cmd": ["private"]}])
def test_prompt_and_command_fields_cannot_enter_receipts(receipts, details):
    assert attempts._apply({
        "tracking_dir": str(receipts), "attempt_id": uuid4().hex, "operation_id": uuid4().hex,
        "phase": "resolution-requested", "details": details,
    }).get("error")
    assert not (receipts / "launch-attempts").exists()


@pytest.mark.parametrize("options", [
    ["--new", "--dry-run"], ["--new", "--base"], [],
    ["--launch-request-status", "c" * 32],
])
def test_new_capability_rejects_invalid_requests_before_resolution(receipts, monkeypatch, options):
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    args = parser.parse_args([
        "resolve", "--require-launch-attempt", "--launch-attempt-id", "b" * 32, *options,
    ])
    monkeypatch.setattr(resolve_cli, "_cmd_resolve", lambda *a: pytest.fail("resolution"))
    with output.capture_json_output():
        assert resolve_cli.cmd_resolve(args) == 2
    assert not (receipts / "launch-attempts").exists()


def test_capability_dispatch_preserves_exact_prompt_and_attempt(receipts, monkeypatch):
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    prompt = " exact private fixture\n'\"\u2603 "
    args = parser.parse_args([
        "resolve", "--json", "--new", "--require-launch-attempt",
        "--launch-attempt-id", "b" * 32, "--seed", prompt,
    ])
    def work(received):
        assert received.seed == prompt and received.launch_attempt_id == "b" * 32
        output._json_output({"launch": {"action": "exec", "worktree_id": "new-a", "seed_id": "seed-a"}})
        return 0
    monkeypatch.setattr(resolve_cli, "_cmd_resolve", work)
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(args) == 0
    assert prompt not in captured.getvalue()
    assert all(prompt not in path.read_text(encoding="utf-8")
               for path in (receipts / "launch-attempts").rglob("*.json"))


@pytest.mark.parametrize("seed", [None, "exact private creation fixture\n'\"\u2603"])
def test_real_creation_then_resume_keeps_one_attempt_and_allocation(
    receipts, monkeypatch, seed,
):
    from agent_worktrees import launch_seed_state, tracking
    from test_resolve_seed_delivery import _args, _stub_launch_plumbing
    from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals

    config = _create_config(receipts)
    _stub_create_worktree_core_internals(monkeypatch, receipts, config)
    _stub_launch_plumbing(monkeypatch, config)
    attempt_id = uuid4().hex
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(_args(
            new_worktree=True, worktree_id=None, dry_run=False,
            launch_attempt_id=attempt_id, require_launch_attempt=True, seed=seed,
        )) == 0
    first = json.loads(captured.getvalue())["launch"]
    wid = first["worktree_id"]
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(_args(
            worktree_id=wid, dry_run=False,
            launch_attempt_id=attempt_id, require_launch_attempt=True,
            seed_id=first["seed_id"] if seed else None,
        )) == 0
    second = json.loads(captured.getvalue())["launch"]
    assert first["launch_attempt_id"] == second["launch_attempt_id"] == attempt_id
    assert first["worktree_id"] == second["worktree_id"]
    assert first["seed_id"] == second["seed_id"]
    assert len(tracking.list_records(receipts)) == 1
    assert second["seed_pending"] is bool(seed)
    current = launch_seed_state.peek(receipts / (wid + ".yaml"))
    assert (current.text if current else None) == seed
    for launch in (first, second):
        saved = attempts.read(receipts, attempt_id, launch["launch_attempt_receipt"]["operation_id"])
        assert saved["events"][-1]["details"]["worktree_id"] == wid
        assert saved["events"][-1]["phase"] == "plan-ready"
    assert first["launch_attempt_receipt"]["operation_id"] != second["launch_attempt_receipt"]["operation_id"]


def test_flush_failure_blocks_resolution_before_effects(receipts, monkeypatch):
    def fail_flush(*args):
        raise OSError("private disk fixture")
    monkeypatch.setattr(attempts.os, "fsync", fail_flush)
    with output.capture_json_output() as captured:
        assert attempts.resolve(
            argparse.Namespace(launch_attempt_id=uuid4().hex), lambda: pytest.fail("effect"),
        ) == 3
    assert "no resolution started" in captured.getvalue()
    assert not list(receipts.rglob("*.tmp"))
    assert not list(receipts.rglob("*.json"))


def test_linked_receipt_is_refused_without_replacing_it(receipts):
    attempt_id, operation_id = uuid4().hex, uuid4().hex
    _run(attempt_id, {"action": "exec"})
    path = attempts._path(receipts, attempt_id, operation_id)
    original = receipts / "original.json"
    original.write_text("foreign fixture", encoding="utf-8")
    os.link(original, path)
    result = attempts._apply({
        "tracking_dir": str(receipts), "attempt_id": attempt_id, "operation_id": operation_id,
        "phase": "resolution-requested", "details": {},
    })
    assert result.get("error")
    assert original.read_text(encoding="utf-8") == "foreign fixture"
