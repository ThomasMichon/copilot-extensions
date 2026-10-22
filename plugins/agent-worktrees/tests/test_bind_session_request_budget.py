"""Explicit registration acknowledgements use the CLI budget, not hook latency.

Reuse the receiving-state suite and preserve the authority repair credited to
ThomasMichon/copilot-extensions#6586. This caller-budget witness does not close
the assigned, broader acceptance of ThomasMichon/copilot-extensions#6579.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import (
    __main__ as cli,
    locks,
    native_lifecycle_source,
    output,
    profile_assignment,
    session_binding_cli as binding,
    status_monitor_runtime,
    tracking,
    tracking_session_registry,
    tracking_write,
    worktree_identity,
)
from test_tracking_session_registration_write import record_path as record_path


@pytest.fixture
def registration_record(monkeypatch_config, request):
    # Reuse the receiving-state fixture after its temporary project is selected.
    return request.getfixturevalue("record_path")


@pytest.fixture
def bind_cli(registration_record, monkeypatch):
    captured = {}
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(native_lifecycle_source, "witness", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native_lifecycle_source, "start_identity", lambda *_args: None)
    monkeypatch.setattr(profile_assignment, "bind", lambda *_args: None)
    monkeypatch.setattr(binding.activity, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(binding, "_emit_handoff_claim_stages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(binding, "_note_disposition_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(binding, "_register_manager_owned_monitor_session", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(binding, "_spawn_status_updater", lambda *_args: True)
    monkeypatch.setattr(output, "_json_output", captured.update)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)

    def invoke(session_id):
        args = cli.build_parser().parse_args([
            "bind-session", "--worktree-id", "wt-reg", "--session-id", session_id,
        ])
        assert args.command == "bind-session"
        return binding.cmd_bind_session(args)

    return invoke, captured


def _proof(case, *, record_path, **fields):
    if path := os.environ.get("BIND_BUDGET_PROOF"):
        raw = record_path.read_bytes()
        fields["persisted_record_utf8"] = raw.decode("utf-8")
        fields["record_sha256"] = hashlib.sha256(raw).hexdigest()
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"case": case, **fields}) + "\n")


@contextmanager
def _receiver(monkeypatch, *, delay=0.0, gate=None, fail_after_commit=False):
    calls, saved, requests = [], [], []
    entered, completed = threading.Event(), threading.Event()
    real_save = tracking.save_record
    real_send = tracking_write._send_tracking_write_request

    def save(record, path=None, **kwargs):
        result = real_save(record, path, **kwargs)
        saved.append(time.monotonic())
        return result

    def send(*args, **kwargs):
        requests.append({key: kwargs[key] for key in ("key", "request_deadline_s")})
        try:
            return real_send(*args, **kwargs)
        except tracking_write.AmbiguousWriteOutcome:
            requests[-1]["outcome"] = "AmbiguousWriteOutcome"
            raise

    def compute(kind, payload):
        assert payload["verb"] == "session_register"
        calls.append(payload)
        entered.set()
        try:
            if gate is not None:
                assert gate.wait(timeout=tracking_write.REQUEST_DEADLINE_S)
            elif delay:
                time.sleep(delay)
            result = tracking_write.compute(kind, payload)
            if fail_after_commit:
                raise RuntimeError("synthetic acknowledgement failure after persistence")
            return result
        finally:
            completed.set()

    monkeypatch.setattr(tracking, "save_record", save)
    monkeypatch.setattr(tracking_write, "_send_tracking_write_request", send)
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda *_args, **_kwargs: pytest.fail("a sent registration must not retry directly"),
    )
    server = tracking_write.start_server(compute)
    server.start()
    fields = tracking_write.rendezvous_fields(server)
    assert fields["tracking_write_verb_versions"]["session_register"] == 3
    monkeypatch.setattr(locks, "read_lock", lambda _path: fields)
    state = SimpleNamespace(
        calls=calls, saved=saved, requests=requests, entered=entered, completed=completed,
    )
    try:
        yield state
    finally:
        if gate is not None:
            gate.set()
        try:
            if entered.is_set():
                assert completed.wait(timeout=5)
        finally:
            server.close()


def test_real_parser_bind_selects_existing_cli_budget(registration_record, bind_cli, monkeypatch):
    calls = []

    def dispatch(verb, args, **kwargs):
        calls.append((verb, kwargs))
        return tracking_write.compute(tracking_write.KIND, {"verb": verb, "args": args})

    monkeypatch.setattr(tracking_write, "dispatch", dispatch)
    invoke, captured = bind_cli
    assert invoke("session-explicit-budget") == 0
    assert captured["is_head"] is True
    assert calls[0][0] == "session_register"
    assert calls[0][1]["request_deadline_s"] == tracking_write.REQUEST_DEADLINE_S
    assert calls[0][1]["boot_wait_s"] == 0.0
    assert calls[0][1]["min_version"] == 3
    assert len(calls) == 1


@pytest.mark.parametrize("source", ["hook", "bind", "handoff"])
def test_registration_default_budget_is_not_selected_by_source_text(
    registration_record, monkeypatch, source,
):
    calls = []
    monkeypatch.setattr(
        tracking_write, "dispatch",
        lambda verb, args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    )
    tracking.register_session("wt-reg", "session-default", source=source)
    assert calls[0][0]["source"] == source
    assert calls[0][1]["request_deadline_s"] == 1.5
    assert calls[0][1]["boot_wait_s"] == 0.0 and calls[0][1]["min_version"] == 3
    assert len(calls) == 1


@pytest.mark.parametrize("delay", [0.0, 3.0], ids=["fast", "beyond-hook-budget"])
def test_real_explicit_client_gets_original_reply_and_one_persisted_registration(
    registration_record, bind_cli, monkeypatch, delay,
):
    invoke, captured = bind_cli
    with _receiver(monkeypatch, delay=delay) as receiver:
        started = time.monotonic()
        rc = invoke("session-explicit")
        returned_at = time.monotonic()
        before_complete = tracking.load_record(registration_record).session_entry("session-explicit")
        assert receiver.completed.wait(timeout=5)
        record = tracking.load_record(registration_record)
        entry = record.session_entry("session-explicit")
        _proof(
            f"explicit-{delay}", record_path=registration_record, cli_returncode=rc, delay=delay,
            elapsed=returned_at-started, request_deadline=receiver.requests[0]["request_deadline_s"],
            persisted_when_client_returned=before_complete is not None,
            persisted_after_owner_completed=entry is not None,
            owner_computes=len(receiver.calls), record_saves=len(receiver.saved),
            sent_requests=len(receiver.requests),
            client_error=captured.get("error"),
        )
        assert entry is not None and len(record.sessions) == len(entry.activations) == 1
        assert len(receiver.calls) == len(receiver.saved) == len(receiver.requests) == 1
        assert len(receiver.requests[0]["key"]) == 32
        assert rc == 0, "explicit CLI returned an ambiguous failure despite one persisted registration"
        assert captured["is_head"] is True
        assert before_complete is not None
        assert receiver.requests[0]["request_deadline_s"] == tracking_write.REQUEST_DEADLINE_S
        if delay:
            assert tracking_session_registry._SESSION_REGISTER_REQUEST_DEADLINE_S < delay < tracking_write.REQUEST_DEADLINE_S


def test_real_hook_is_bounded_ambiguous_then_original_owner_persists_once(
    registration_record, monkeypatch,
):
    gate = threading.Event()
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    with _receiver(monkeypatch, gate=gate) as receiver:
        started = time.monotonic()
        with pytest.raises(tracking_write.AmbiguousWriteOutcome):
            tracking.register_session("wt-reg", "session-hook")
        returned_at = time.monotonic()
        assert receiver.entered.is_set() and not receiver.completed.is_set()
        assert tracking.load_record(registration_record).session_entry("session-hook") is None
        gate.set()
        assert receiver.completed.wait(timeout=5)
        record = tracking.load_record(registration_record)
        entry = record.session_entry("session-hook")
        assert entry is not None and len(record.sessions) == len(entry.activations) == 1
        assert len(receiver.calls) == len(receiver.saved) == len(receiver.requests) == 1
        assert receiver.saved[0] > returned_at
        assert receiver.requests[0]["request_deadline_s"] == tracking_session_registry._SESSION_REGISTER_REQUEST_DEADLINE_S == 1.5
        assert returned_at-started < tracking_write.REQUEST_DEADLINE_S
        _proof(
            "hook-later-persist", record_path=registration_record, elapsed=returned_at-started, deadline=1.5,
            client_outcome="AmbiguousWriteOutcome", absent_at_client_return=True,
            actual_save_after_client_return=True, owner_computes=1, record_saves=1,
            sent_requests=1, automatic_direct_retry=False,
        )


def test_real_explicit_client_preserves_receiving_authority_refusal(
    registration_record, bind_cli, monkeypatch,
):
    record = tracking.load_record(registration_record)
    record.repo = "unregistered-project"
    tracking.save_record(record, registration_record)
    before = registration_record.read_bytes()
    invoke, captured = bind_cli
    with _receiver(monkeypatch) as receiver:
        assert invoke("session-refused") == 1
        assert "receiving-side authority" in captured["error"]
        assert registration_record.read_bytes() == before
        assert "bound" not in captured
        assert len(receiver.calls) == len(receiver.requests) == 1
        assert receiver.saved == []


def test_real_explicit_client_keeps_post_commit_reply_failure_ambiguous_without_retry(
    registration_record, bind_cli, monkeypatch,
):
    invoke, captured = bind_cli
    with _receiver(monkeypatch, fail_after_commit=True) as receiver:
        assert invoke("session-ambiguous") == 1
        assert captured["error"].startswith("failed to bind session:")
        assert "bound" not in captured
        assert tracking.load_record(registration_record).session_entry("session-ambiguous") is not None
        assert len(receiver.calls) == len(receiver.saved) == len(receiver.requests) == 1
        assert receiver.requests[0]["request_deadline_s"] == tracking_write.REQUEST_DEADLINE_S
        assert receiver.requests[0]["outcome"] == "AmbiguousWriteOutcome"
