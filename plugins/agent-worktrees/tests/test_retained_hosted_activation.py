"""Explicit hosted evidence never substitutes a CLI-native hold witness."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import config as cfg, native_source_context as context
from agent_worktrees import native_lifecycle_source, resource_relationship, session_binding_cli, tracking
from agent_worktrees import retained_record_writer
from agent_worktrees import tracking_session_registration_write as registration
from agent_worktrees import tracking_session_deregistration_write as deregistration
from test_tracking_session_registration_write import record_path as record_path

SID = "00000000-0000-4000-8000-000000000001"
EPOCH = {"platform": "linux", "environment_id": "e" * 32, "boot_id": SID}
REAL_EPOCH = context.epoch
REAL_PROCESS = context.process


@pytest.mark.parametrize("retired", [False, True])
def test_extracted_handoff_reporting_keeps_stage_and_callback_contract(
    monkeypatch: pytest.MonkeyPatch, retired: bool,
) -> None:
    from agent_worktrees import activity, handoff_diagnostics

    events: list[str] = []
    stamps: list[tuple[Any, dict[str, Any]]] = []
    callbacks: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(activity, "log_event", lambda name, **_: events.append(name))
    monkeypatch.setattr(
        activity, "read_events",
        lambda **_: [{"handoff_token": "handoff-token", "outcome": "gone"}] if retired else [],
    )
    monkeypatch.setattr(
        handoff_diagnostics, "stamp_session_state_handoff",
        lambda *args, **kwargs: stamps.append((args, kwargs)),
    )
    monkeypatch.setattr(
        session_binding_cli, "_maybe_emit_stage_13",
        lambda *args, **kwargs: callbacks.append((args, kwargs)),
    )
    handoff = cast(tracking.SessionHandoff, SimpleNamespace(
        token="handoff-token", predecessor="previous-native",
    ))
    session_binding_cli._emit_handoff_claim_stages(
        "workspace", "current-native", handoff, launch_id="original-launch",
    )
    assert events == ["handoff_successor_claimed"] + (
        [] if retired else ["handoff_pickup_confirmed_predecessor_closing"]
    )
    assert stamps == [(("previous-native",), {
        "handoff_token": "handoff-token", "successor_session_id": "current-native",
    })]
    assert callbacks == [(("workspace", "handoff-token"), {"launch_id": "original-launch"})]
    events.clear()
    session_binding_cli._emit_handoff_claim_stages("workspace", "current-native", None)
    assert events == [] and len(stamps) == len(callbacks) == 1


@pytest.mark.parametrize("state", ["Z", "X", "x"])
def test_exited_process_cannot_supply_a_hosted_activation(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    fields = [state, "1", *(["0"] * 17), "100"]
    monkeypatch.setattr(context, "_text", lambda *_: "101 (worker) " + " ".join(fields))
    assert context.process(101)["start_time"] == "100"
    with pytest.raises(ValueError, match="exited"):
        context.process(101, require_live=True)


@pytest.fixture
def hosted(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> SimpleNamespace:
    args = {
        "worktree_id": "wt-reg", "yaml_path": str(record_path), "session_id": SID,
        "source": "hook:new", "started_at": "first", "source_identity": "native:previous",
    }
    assert registration.apply_session_register(args)["ok"]
    assert deregistration.apply_session_deregister({
        **args, "ended_at": "exit",
    })["lifecycle"] == "ended"
    record = tracking.load_record(record_path)
    record.creation_nonce = "original-incarnation"
    tracking.save_record(record, record_path)
    identity = {"installation_root": str(tmp_path / "owner"), "runtime_generation": "a" * 32}
    monkeypatch.setattr(resource_relationship, "writer_identity", lambda: identity)
    monkeypatch.setattr(cfg, "active_project", lambda: "test-repo")
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _: record_path.parent)
    monkeypatch.setattr(
        session_binding_cli, "_ensure_status_monitor",
        lambda: pytest.fail("explicit hosted registration must not start infrastructure"),
    )
    monkeypatch.setattr(
        native_lifecycle_source, "session_process_owns_hold",
        lambda *_: pytest.fail("hosted evidence must not fabricate or require a CLI hold"),
    )
    scope = {
        "version": 1, "identity": identity, "creation_nonce": record.creation_nonce,
        "worktree_id": record.worktree_id, "project": record.repo,
        "worktree_path": record.worktree_path,
    }
    packet = {
        "version": 1, "operation_id": "original-recovery", "session_id": SID,
        "head_revision": record.head_revision, "epoch": EPOCH,
        "host": {"pid": 101, "start_time": "100"},
        "child": {"pid": 102, "start_time": "200"},
    }
    processes = {
        101: {"pid": 101, "start_time": "100", "parent_pid": 1},
        102: {"pid": 102, "start_time": "200", "parent_pid": 101},
    }
    monkeypatch.setattr(context, "epoch", lambda: EPOCH)
    monkeypatch.setattr(
        context, "process", lambda pid, **_: processes[pid].copy(),
    )
    parser = argparse.ArgumentParser()
    session_binding_cli.add_parsers(parser.add_subparsers(dest="command"))

    def invoke(
        *, owner: dict[str, Any] | None = scope, evidence: dict[str, Any] = packet,
        extra: tuple[str, ...] = (),
    ) -> int:
        argv = [
            "register-session", "--session-id", SID, "--worktree-id", record.worktree_id,
            "--cwd", record.worktree_path, "--pid", str(evidence["child"]["pid"]),
            "--retained-hosted-activation", json.dumps(evidence), *extra,
        ]
        if owner is not None:
            argv += ["--retained-record-writer", json.dumps(owner)]
        return session_binding_cli.cmd_register_session(parser.parse_args(argv))

    return SimpleNamespace(
        path=record_path, packet=packet, scope=scope, invoke=invoke, processes=processes,
    )


def test_hosted_activation_is_idempotent_and_does_not_reopen_closed_source(
    hosted: SimpleNamespace,
) -> None:
    assert hosted.invoke() == 0
    before = hosted.path.read_bytes()
    assert hosted.invoke() == 0
    assert hosted.path.read_bytes() == before
    entry = tracking.load_record(hosted.path).session_entry(SID)
    assert entry is not None and len(entry.activations) == 2
    assert entry.activations[0].source_identity == "native:previous"
    assert entry.activations[0].ended_at == "exit"
    latest = entry.activations[-1]
    assert latest.start_source == "retained-hosted-acp"
    assert latest.source_identity is not None
    assert latest.source_identity.startswith("hosted-acp-source-v1:")
    assert latest.ended_at is None and entry.pid == 102
    assert deregistration.apply_session_deregister({
        "worktree_id": "wt-reg", "yaml_path": str(hosted.path), "session_id": SID,
        "source_identity": latest.source_identity, "ended_at": "second-exit",
    })["lifecycle"] == "ended"
    ended = hosted.path.read_bytes()
    assert hosted.invoke(evidence={**hosted.packet, "operation_id": "different-recovery"}) != 0
    assert hosted.path.read_bytes() == ended


def test_missing_hosted_record_is_not_successful_registration(hosted: SimpleNamespace) -> None:
    hosted.path.unlink()
    assert hosted.invoke() != 0
    assert not hosted.path.exists()


def test_same_open_hosted_source_retries_after_save_prunes_older_history(
    hosted: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracking, "_MAX_SESSION_ACTIVATIONS", 1)
    assert hosted.invoke() == 0
    record = tracking.load_record(hosted.path)
    entry = record.session_entry(SID)
    assert entry is not None and len(entry.activations) == 1
    assert entry.pruned_sources == ["native:previous"]
    before = hosted.path.read_bytes()
    assert hosted.invoke() == 0
    assert hosted.path.read_bytes() == before


@pytest.mark.parametrize("change", ["duplicate-source", "stale-ended"])
def test_same_open_source_requires_canonical_unique_observation(
    hosted: SimpleNamespace, change: str,
) -> None:
    assert hosted.invoke() == 0
    record = tracking.load_record(hosted.path)
    entry = record.session_entry(SID)
    assert entry is not None
    if change == "duplicate-source":
        entry.activations[0].source_identity = entry.activations[-1].source_identity
    else:
        entry.ended_at = "stale"
    tracking.save_record(record, hosted.path)
    loaded = tracking.load_record(hosted.path).session_entry(SID)
    assert loaded is not None
    if change == "duplicate-source":
        assert loaded.activations[0].source_identity == loaded.activations[-1].source_identity
        assert not any(item.source_ambiguous for item in loaded.activations)
    else:
        assert loaded.ended_at == "stale" and loaded.activations[-1].ended_at is None
    before = hosted.path.read_bytes()
    assert hosted.invoke() != 0
    assert hosted.path.read_bytes() == before


@pytest.mark.parametrize("change", [
    "writer", "nonce", "head", "epoch", "birth", "parent", "exited", "open-source",
    "pruned", "saturated", "terminal", "missing-scope", "hook", "extra-field", "older-open",
])
def test_changed_or_unqualified_hosted_activation_cannot_write(
    hosted: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    packet, scope = copy.deepcopy(hosted.packet), copy.deepcopy(hosted.scope)
    extra: tuple[str, ...] = ()
    if change == "writer":
        scope["identity"]["runtime_generation"] = "b" * 32
    elif change == "nonce":
        scope["creation_nonce"] = "changed"
    elif change == "head":
        packet["head_revision"] += 1
    elif change == "epoch":
        packet["epoch"]["boot_id"] = "changed"
    elif change == "birth":
        hosted.processes[102]["start_time"] = "changed"
    elif change == "parent":
        hosted.processes[102]["parent_pid"] = 999
    elif change == "exited":
        def exited(*_args: Any, **_kwargs: Any) -> Any:
            raise ProcessLookupError("original child exited")
        monkeypatch.setattr(context, "process", exited)
    elif change == "missing-scope":
        scope = None
    elif change == "hook":
        extra = ("--stdin",)
    elif change == "extra-field":
        packet["accepted"] = True
    else:
        record = tracking.load_record(hosted.path)
        entry = record.session_entry(SID)
        assert entry is not None
        if change == "open-source":
            entry.activations[-1].ended_at = None
            entry.ended_at = None
        elif change == "older-open":
            entry.activations[-1].ended_at = None
            entry.activations.append(tracking.SessionActivation(
                ordinal=2, started_at="later", start_recorded_at="later",
                source_identity="native:later", ended_at="later-exit",
            ))
        elif change == "pruned":
            entry.pruned_sources = ["native:pruned"]
        elif change == "saturated":
            entry.source_history_saturated = True
        elif change == "terminal":
            record.kind, record.status = "system", "finalized"
        tracking.save_record(record, hosted.path)
    before = hosted.path.read_bytes()
    assert hosted.invoke(owner=scope, evidence=packet, extra=extra) != 0
    assert hosted.path.read_bytes() == before


def test_actual_host_and_child_processes_need_no_native_hold(
    hosted: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("Hosted activation process corroboration requires Linux procfs")
    # Restore the actual physical readers; this is a process-boundary control,
    # not proof that this synthetic child has loaded an ACP conversation.
    monkeypatch.setattr(context, "epoch", REAL_EPOCH)
    monkeypatch.setattr(context, "process", REAL_PROCESS)
    from agent_worktrees import retained_hosted_activation

    record = tracking.load_record(hosted.path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        packet = {
            **hosted.packet, "epoch": context.epoch(),
            "host": {key: context.process(os.getpid())[key] for key in ("pid", "start_time")},
            "child": {key: context.process(child.pid)[key] for key in ("pid", "start_time")},
        }
        monkeypatch.setattr(
            retained_record_writer, "active", lambda: True,
        )
        token = retained_hosted_activation.source_identity(record, SID, child.pid, packet)
        assert token.startswith("hosted-acp-source-v1:")
        assert child.poll() is None
        with pytest.raises(ValueError, match="epoch changed"):
            retained_hosted_activation.source_identity(
                record, SID, child.pid,
                {**packet, "host": {**packet["host"], "start_time": "0"}},
            )
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=10)
        if child.stderr is not None:
            child.stderr.close()
