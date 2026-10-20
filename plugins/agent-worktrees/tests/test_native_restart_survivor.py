"""Current original hosting is evidence, not adoption or seed acknowledgement."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import native_hosting_evidence, native_source_context
from agent_worktrees import session_metadata_cli, sessions, tracking


NATIVE = "00000000-0000-4000-8000-000000000001"
TARGET = {"platform": "linux", "environment_id": "b" * 32, "boot_id": "current"}
SOURCE = {**TARGET, "boot_id": "previous"}


@pytest.fixture
def subject(sample_record, tmp_path, monkeypatch):
    from agent_worktrees import native_restart_survivor as owner

    worktree = tmp_path / "checkout"
    worktree.mkdir()
    original = native_source_context.identity(NATIVE, 42, "123", SOURCE)
    current = native_source_context.identity(NATIVE, 101, "456", TARGET)
    record = replace(
        sample_record, worktree_path=str(worktree), platform="linux",
        creation_nonce="0123456789abcdef", head_revision=5, lifecycle_revision=10,
        head_session=NATIVE, sessions=[tracking.SessionEntry(
            NATIVE, "original", activations=[
                tracking.SessionActivation(
                    2, "original", "original", source_identity=original,
                    ended_at="next", end_source="inferred:next-start",
                ),
                tracking.SessionActivation(3, "next", "next", source_identity=current),
            ],
        )],
    )
    request = {
        "request_id": str(uuid4()), "source_epoch": SOURCE, "native_incarnation": "42:123",
        "expected": {
            "project": record.repo, "worktree_id": record.worktree_id,
            "worktree_path": str(worktree), "native_session_id": NATIVE,
            "worktree_incarnation": record.creation_nonce, "head_revision": 5,
            "owner_revision": 8, "activation_ordinal": 2, "source_identity": original,
            "target_epoch": TARGET,
        },
    }
    monkeypatch.setattr(native_source_context, "epoch", lambda: dict(TARGET))
    monkeypatch.setattr(owner.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(native_hosting_evidence.cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(sessions, "session_has_conversation_data", lambda _: True)
    monkeypatch.setattr(native_hosting_evidence, "session_liveness", lambda _: "live")
    monkeypatch.setattr(native_hosting_evidence, "session_process_owns_hold", lambda *a: True)
    monkeypatch.setattr(sessions, "mux_binding_for_session", lambda *a, **kw: {
        "session_name": sessions.mux_session_name(record.worktree_id),
        "pane_id": "%1", "copilot_pid": 101, "copilot_start_time": "456",
        "pane_start_time": "455", "session_incarnation": "$1:100",
    })
    monkeypatch.setattr(sessions, "mux_new_session", lambda *a, **kw: pytest.fail("launch"))
    monkeypatch.setattr(sessions, "mux_seed_pane", lambda *a, **kw: pytest.fail("seed"))
    return owner, record, request


def test_exact_original_survivor_is_current_not_adopted_or_acknowledged(subject):
    owner, record, request = subject
    before = asdict(record)
    result = owner.inspect(request, loader=lambda: record)
    assert result["survivor_confirmed"] is True
    assert result["reason"] == "original-native-survivor-confirmed"
    assert result["hosting"] == {
        "native_session_id": NATIVE, "target_epoch": TARGET,
        "head_revision": 5, "owner_revision": 10, "activation_ordinal": 3,
        "source_identity": record.sessions[0].activations[-1].source_identity,
        "native_incarnation": "101:456", "session_incarnation": "$1:100",
    }
    assert result["request"] == request
    assert all(result[field] is False for field in (
        "launch_authorized", "reuse_authorized", "seed_acknowledged",
        "restored", "automatic_eligible",
    ))
    assert asdict(record) == before


@pytest.mark.parametrize("damage", [
    "hold", "mux", "unknown", "dead", "source", "ended", "history", "pruned",
    "head", "owner", "epoch", "claims", "task", "inbound", "profile", "paused",
    "duplicate-original", "saturated", "activation", "incarnation", "path",
])
def test_stale_other_or_unqualified_facts_never_confirm_survivor(subject, monkeypatch, damage):
    owner, record, request = subject
    if damage == "hold":
        monkeypatch.setattr(native_hosting_evidence, "session_process_owns_hold", lambda *a: False)
    elif damage == "mux":
        monkeypatch.setattr(sessions, "mux_binding_for_session", lambda *a, **kw: None)
    elif damage in {"dead", "unknown"}:
        monkeypatch.setattr(native_hosting_evidence, "session_liveness", lambda _: damage)
    elif damage == "source":
        record.sessions[0].activations[-1].source_identity = "native-source-v1:" + "c" * 64
    elif damage == "ended":
        record.sessions[0].activations[-1].ended_at = "exit"
    elif damage == "history":
        record.sessions[0].activations[0].end_reason = "user_exit"
    elif damage == "pruned":
        record.sessions[0].pruned_sources = [request["expected"]["source_identity"]]
    elif damage == "duplicate-original":
        record.sessions[0].activations.insert(1, replace(
            record.sessions[0].activations[0], ordinal=1,
        ))
    elif damage == "saturated":
        record.sessions[0].source_history_saturated = True
    elif damage == "activation":
        record.sessions[0].activations[-1].ordinal = 2
    elif damage == "incarnation":
        record.creation_nonce = "fedcba9876543210"
    elif damage == "path":
        record.worktree_path += "/missing"
    elif damage == "head":
        record.head_session = str(uuid4())
        record.head_revision += 1
    elif damage == "owner":
        record.lifecycle_revision = request["expected"]["owner_revision"]
    elif damage == "epoch":
        monkeypatch.setattr(native_source_context, "epoch", lambda: {**TARGET, "boot_id": "other"})
    elif damage in {"claims", "task"}:
        record.resources = [tracking.ResourceClaim(
            kind="task" if damage == "task" else "session", ref="other/owner/worktree",
        )]
    elif damage == "inbound":
        record.owner_ref = "other/owner/worktree"
    elif damage == "profile":
        record.bound_agent = "other-profile"
    else:
        record.paused = True
    result = owner.inspect(request, loader=lambda: record)
    assert result["survivor_confirmed"] is False
    assert "hosting" not in result
    assert result["launch_authorized"] is False and result["reuse_authorized"] is False
    assert result["seed_acknowledged"] is False


def test_exact_active_self_claim_is_preserved(subject):
    owner, record, request = subject
    record.resources = [tracking.ResourceClaim(
        kind="session", ref=tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=NATIVE,
        ),
    )]
    before = asdict(record)
    assert owner.inspect(request, loader=lambda: record)["survivor_confirmed"] is True
    assert asdict(record) == before


@pytest.mark.parametrize("change", ["owner", "epoch"])
def test_change_during_hosting_probe_invalidates_result(subject, monkeypatch, change):
    owner, record, request = subject
    original = native_hosting_evidence.observe
    reads = [record]

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "owner":
            reads[0] = replace(record, paused=True)
        else:
            monkeypatch.setattr(native_source_context, "epoch", lambda: {**TARGET, "boot_id": "other"})
        return result

    monkeypatch.setattr(native_hosting_evidence, "observe", observe)
    assert owner.inspect(request, loader=lambda: reads[0])["survivor_confirmed"] is False


def test_malformed_request_never_reads_owner(subject):
    owner, record, request = subject
    request = deepcopy(request)
    request["expected"]["native_session_id"] = "not-a-uuid"
    with pytest.raises(ValueError):
        owner.inspect(request, loader=lambda: pytest.fail("invalid input read"))


def test_cli_uses_explicit_request_and_preserves_persisted_owner(subject, tmp_path, monkeypatch, capfd):
    from agent_worktrees import __main__ as core

    owner, record, request = subject
    path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    before = {item.name: item.read_bytes() for item in tmp_path.iterdir() if item.is_file()}
    monkeypatch.setattr(session_metadata_cli.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", str(uuid4()))
    args = core.build_parser().parse_args([
        "session-role", "--restart-survivor", json.dumps(request), "--json",
    ])
    assert session_metadata_cli.cmd_session_role(args) == 0
    assert json.loads(capfd.readouterr().out)["survivor_confirmed"] is True
    assert {item.name: item.read_bytes() for item in tmp_path.iterdir() if item.is_file()} == before


@pytest.mark.parametrize("raw", ["{}", "not-json", '"' + "a" * 8192 + '"'])
def test_invalid_cli_request_is_visible_without_owner_read(subject, monkeypatch, capfd, raw):
    from agent_worktrees import __main__ as core

    monkeypatch.setattr(tracking, "load_record", lambda *a: pytest.fail("invalid input read"))
    args = core.build_parser().parse_args(["session-role", "--restart-survivor", raw, "--json"])
    assert session_metadata_cli.cmd_session_role(args) != 0
    assert "survivor" in json.loads(capfd.readouterr().out)["error"]


def test_actual_owner_child_reads_exact_persisted_survivor_without_writing(subject, tmp_path):
    owner, record, request = subject
    path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    before = path.read_bytes()
    code = """
import json, sys
from pathlib import Path
from agent_worktrees import __main__ as core
from agent_worktrees import native_hosting_evidence as hosting, native_source_context
from agent_worktrees import session_metadata_cli as cli, sessions
request = json.loads(sys.argv[1])
expected = request["expected"]
cli.cfg.tracking_dir = lambda: Path(sys.argv[2]).parent
hosting.cfg.detect_platform = lambda: "linux"
native_source_context.epoch = lambda: expected["target_epoch"]
sessions.session_has_conversation_data = lambda _: True
hosting.session_liveness = lambda _: "live"
hosting.session_process_owns_hold = lambda *a: True
sessions.mux_binding_for_session = lambda *a, **kw: {
    "session_name": sessions.mux_session_name(expected["worktree_id"]),
    "pane_id": "%1", "copilot_pid": 101, "copilot_start_time": "456",
    "pane_start_time": "455", "session_incarnation": "$1:100",
}
args = core.build_parser().parse_args(["session-role", "--restart-survivor", sys.argv[1], "--json"])
raise SystemExit(cli.cmd_session_role(args))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, json.dumps(request), str(path)],
        capture_output=True, text=True, timeout=30, check=False, **no_window_kwargs(),
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["survivor_confirmed"] is True and result["request"] == request
    assert result["seed_acknowledged"] is False and result["reuse_authorized"] is False
    assert path.read_bytes() == before


def test_unsupported_epoch_refuses_before_tracking_and_seed_state(subject, monkeypatch):
    owner, record, request = subject
    monkeypatch.setattr(native_source_context, "epoch", lambda: None)
    monkeypatch.setattr(owner.cfg, "tracking_dir", lambda: pytest.fail("unsupported state access"))
    result = owner.inspect(request, loader=lambda: pytest.fail("unsupported owner read"))
    assert result["survivor_confirmed"] is False and result["reason"] == "foreign-environment"


def test_pending_seed_blocks_snapshot_without_claiming_or_finishing_it(subject, tmp_path):
    from agent_worktrees import launch_seed_state

    owner, record, request = subject
    path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    seed = launch_seed_state.state_path(path)
    seed.parent.mkdir()
    seed.write_text(json.dumps({
        "version": 1, "seed_id": str(uuid4()), "kind": "resume", "text": "pending", "revision": 0,
    }))
    before = seed.read_bytes()
    assert owner.inspect(request, loader=lambda: record)["reason"] == "pending-seed"
    assert seed.read_bytes() == before
