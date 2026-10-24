"""Negotiation binds target-owned seed admission without authorizing replay."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
from types import SimpleNamespace

import pytest

from agent_worktrees import launch_request, launch_seed_state, output, remote_seed_launch, resolve_cli, tracking
from test_launch_request import admission_config as admission_config, authority as authority
from test_worktree_creation_seed import _create_config
from test_launch_request import _stub_create_worktree_core_internals


def intent(kind="resume"):
    return {
        "version": 2, "request_id": "a" * 32, "kind": kind,
        "worktree_id": "wt-a" if kind == "resume" else None,
        "text": "exact fixture\n '\";$() \u2603 ", "no_mux": False,
        "attempt_id": "b" * 32, "native_seed_required": True,
    }


def encode(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def parse(options):
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers(dest="command"))
    return parser.parse_args(["resolve", *options])


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_native_target_retains_correlation_through_replay_and_handoff(
    tmp_path, authority, monkeypatch, kind,
):
    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    if kind == "resume":
        tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                   config.repo_name, "test", "windows", tmp_path)
    request = intent(kind)
    assert launch_request.decode(encode(request)) == request
    first = launch_request.execute(config, request)
    assert first == launch_request.execute(config, request)
    assert first["version"] == 2 and first["attempt_id"] == request["attempt_id"]
    assert first["native_seed_required"] is True
    receipt = launch_request._read_admission(tmp_path, request["request_id"])
    assert receipt["attempt_id"] == request["attempt_id"] and "text" not in receipt
    record = tmp_path / f"{first['worktree_id']}.yaml"
    seed = launch_seed_state.peek(record)
    assert seed.text == request["text"]
    taken = launch_seed_state.take(record, seed_id=seed.seed_id)
    status = launch_request.inspect(request["request_id"])
    assert status["attempt_id"] == request["attempt_id"]
    assert status["state"] == "handoff-unconfirmed" and not status["ready"]
    launch_seed_state.finish(record, taken)
    with pytest.raises(ValueError, match="completed"):
        launch_request.execute(config, request)
    assert len(tracking.list_records(tmp_path)) == 1


def test_native_cli_returns_only_bound_target_evidence(tmp_path, authority, monkeypatch, capsys):
    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    load_config = launch_request.cfg.load_config
    monkeypatch.setattr(launch_request.cfg, "load_config",
                        lambda *a, **kw: load_config(*a, **kw) if kw.get("project") else authority)
    assert resolve_cli.cmd_resolve(parse([
        "--json", "--launch-request-b64", encode(intent()), "--require-native-seed",
    ])) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["version"] == 2 and receipt["native_seed_required"] is True
    assert receipt["attempt_id"] == intent()["attempt_id"]
    assert "text" not in receipt and "prompt" not in receipt
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == intent()["text"]


@pytest.mark.parametrize("state", ["active", "unknown"])
def test_negotiated_native_request_still_refuses_target_provider(tmp_path, authority, state):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp", state=state, binding_revision=1, blob={},
    )
    tracking.save_record(record, record.yaml_path)
    before = record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="native execution leg"):
        launch_request.execute(authority, intent())
    assert record.yaml_path.read_bytes() == before
    assert launch_seed_state.peek(record.yaml_path) is None


@pytest.mark.parametrize("change", [
    {"attempt_id": "c" * 32}, {"version": 1}, {"native_seed_required": False},
])
def test_correlation_mutation_cannot_reuse_admission(tmp_path, authority, change):
    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    original = intent()
    launch_request.execute(authority, original)
    before = launch_request._admission_path(tmp_path, original["request_id"]).read_bytes()
    with pytest.raises(ValueError):
        launch_request.execute(authority, {**original, **change})
    assert launch_request._admission_path(tmp_path, original["request_id"]).read_bytes() == before
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == original["text"]


def test_valid_but_changed_stored_attempt_cannot_redirect_replay(tmp_path, authority):
    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    original = intent()
    launch_request.execute(authority, original)
    path = launch_request._admission_path(tmp_path, original["request_id"])
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["attempt_id"] = "c" * 32
    path.write_text(json.dumps(receipt), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="different intent"):
        launch_request.execute(authority, original)
    assert path.read_bytes() == before
    assert launch_seed_state.peek(tmp_path / "wt-a.yaml").text == original["text"]


def test_native_new_lost_receipt_retains_same_attempt_and_allocation(tmp_path, authority, monkeypatch):
    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    dispatch = launch_request._dispatch
    def lose_response(verb, args):
        result = dispatch(verb, args)
        if verb == "launch_request_admit":
            raise OSError("fixture admission response loss")
        return result
    monkeypatch.setattr(launch_request, "_dispatch", lose_response)
    original = intent("new")
    with pytest.raises(OSError, match="response loss"):
        launch_request.execute(config, original)
    receipt = launch_request._read_admission(tmp_path, original["request_id"])
    assert receipt["attempt_id"] == original["attempt_id"]
    assert not tracking.list_records(tmp_path)
    monkeypatch.setattr(launch_request, "_dispatch", dispatch)
    result = launch_request.execute(config, original)
    assert result["worktree_id"] == receipt["worktree_id"]
    assert result["attempt_id"] == original["attempt_id"]
    assert len(tracking.list_records(tmp_path)) == 1


@pytest.mark.parametrize("attempt", [None, "", "B" * 32, "b" * 31, True, []])
def test_decode_requires_exact_native_correlation(attempt):
    value = {**intent(), "attempt_id": attempt}
    if attempt is None:
        assert launch_request.decode(encode(value)) == value
    else:
        with pytest.raises(ValueError):
            launch_request.decode(encode(value))


@pytest.mark.parametrize("change", [
    {"native_seed_required": False}, {"native_seed_required": 1},
    {"version": True}, {"version": 3}, {"extra": "unknown"},
])
def test_decode_rejects_inexact_native_contract(change):
    with pytest.raises(ValueError):
        launch_request.decode(encode({**intent(), **change}))


@pytest.mark.parametrize("version,flag", [(1, True), (2, False)])
def test_capability_mismatch_refuses_before_configuration(monkeypatch, version, flag):
    value = intent()
    if version == 1:
        value = {key: val for key, val in value.items()
                 if key not in {"attempt_id", "native_seed_required"}}
        value["version"] = 1
    monkeypatch.setattr(launch_request.cfg, "load_config",
                        lambda *a, **kw: pytest.fail("mismatch reached target configuration"))
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(parse([
            "--json", "--launch-request-b64", encode(value),
            *(["--require-native-seed"] if flag else []),
        ])) == 3
    assert "must agree" in json.loads(captured.getvalue())["error"]


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("damage", [None, "version", "attempt_id", "native_seed_required", "missing"])
def test_sender_verifies_target_native_receipt_without_weaker_retry(monkeypatch, shell, damage):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if shell == "pwsh":
            script = base64.b64decode(argv[-1].split()[-1]).decode("utf-16le")
            options = [value.replace("''", "'") for value in re.findall(r"'((?:''|[^'])*)'", script)]
        else:
            options = shlex.split(shlex.split(argv[-1])[-1])
        assert "--require-native-seed" in options
        value = launch_request.decode(options[options.index("--launch-request-b64") + 1])
        assert value == {**intent(), "request_id": value["request_id"]}
        receipt = {
            "version": 2, "request_id": value["request_id"], "seed_id": value["request_id"],
            "seed_kind": "resume", "worktree_id": "wt-a", "attempt_id": value["attempt_id"],
            "native_seed_required": True,
        }
        if damage == "missing":
            del receipt["attempt_id"]
        elif damage:
            receipt[damage] = {"version": 1, "attempt_id": "c" * 32,
                               "native_seed_required": False}[damage]
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt))
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", run)
    args = ["--worktree-id", "wt-a", "--require-native-seed", "--launch-attempt-id=" + "b" * 32]
    if damage:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            remote_seed_launch.prepare(None, "target", shell, args, intent()["text"])
    else:
        plan = remote_seed_launch.prepare(None, "target", shell, args, intent()["text"])
        assert plan["launch_attempt_id"] == "b" * 32
        assert plan["launch_request_version"] == 2
        assert intent()["text"] not in plan["remote_command"]
        if shell == "pwsh":
            command = base64.b64decode(plan["remote_command"].split()[-1]).decode("utf-16le")
        else:
            command = plan["remote_command"]
        assert "--require-native-seed" in command and "--seed-id" in command
    assert len(calls) == 1


@pytest.mark.parametrize("options", [
    [], ["--json", "--new"], ["--json", "--new", "--machine", "target"],
    ["--json", "--new", "--machine", "target", "--seed", "fixture"],
])
def test_attempt_identity_cannot_be_silently_ignored(monkeypatch, options):
    monkeypatch.setattr(resolve_cli.ResolveCommandState, "from_args",
                        lambda *a: pytest.fail("invalid correlation reached resolution"))
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(parse([*options, "--launch-attempt-id", "b" * 32])) == 2
    assert "correlation requires" in json.loads(captured.getvalue())["error"]
