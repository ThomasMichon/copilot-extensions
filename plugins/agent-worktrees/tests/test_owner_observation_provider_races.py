"""Admission epochs and publication races at the selected provider boundary."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_owner_observation_provider import enroll, owner as owner, request, row
from test_owner_observation_runtime import record, runtime as runtime

from agent_worktrees import owner_observation_contract as contract
from agent_worktrees import owner_observation_transport as wire
from agent_worktrees.worktrees_runtime import WorktreesRuntime


def reactivate(owner, replacement):
    successor = WorktreesRuntime(owner.store.db_path) if replacement else None
    try:
        if successor is not None:
            successor.activate()
        owner.activate()
        assert owner.observation_read(request())["error"]["code"] == "unready"
        owner.publish("project", [row(title="reactivated")], observed_at=3)
        current = owner.observation_read(request())
        assert current["type"] == "snapshot"
        return current
    finally:
        if successor is not None:
            successor.shutdown()


@pytest.mark.parametrize("operation", ["read", "history", "feed"])
def test_stale_runtime_error_handler_cannot_close_a_new_admission(runtime, monkeypatch, operation):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    native_operations = owner._operations
    entered, finish = threading.Event(), threading.Event()
    method = "page" if operation == "history" else "snapshot"
    original = getattr(owner.history, method)

    def stale(*args, **kwargs):
        if not entered.is_set():
            raise contract.ObservationError("old captured admission is superseded", "superseded")
        return original(*args, **kwargs)

    class Operations:
        def __enter__(self):
            return native_operations.__enter__()

        def __exit__(self, kind, error, traceback):
            result = native_operations.__exit__(kind, error, traceback)
            if isinstance(error, contract.ObservationError) and not entered.is_set():
                entered.set()
                assert finish.wait(5), "stale runtime error fixture was not released"
            return result

    monkeypatch.setattr(owner, "_operations", Operations())
    monkeypatch.setattr(owner.history, method, stale)
    stream = owner.stream(request(), lambda: False) if operation == "feed" else None
    payload = request()
    if operation == "history":
        payload.pop("after_cursor")
        payload.update(
            source_generation=initial["source_generation"], before_cursor=None, limit=64,
        )

    def invoke():
        if stream is not None:
            return next(stream)
        return owner.history_page(payload) if operation == "history" else owner.read(payload)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke)
            try:
                assert entered.wait(2), "old handler did not release its operation lock"
                owner.activate()
                assert owner.read(request()) == initial
            finally:
                finish.set()
            stale_result = future.result(timeout=3)
        assert stale_result["type"] == "error"
        assert stale_result["error"]["code"] == "superseded"
        assert owner.read(request()) == initial
    finally:
        finish.set()
        if stream is not None:
            stream.close()


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("history", [False, True])
def test_stale_point_request_cannot_retire_a_reactivated_admission(
    owner, monkeypatch, replacement, history,
):
    monkeypatch.setattr(owner, "_elected", lambda: True)
    initial = enroll(owner)
    old = owner.observations.runtime
    method = "history_page" if history else "read"
    original = getattr(old, method)
    entered, finish = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            assert finish.wait(5), "stale point fixture was not released"
        return result

    monkeypatch.setattr(old, method, paused)
    payload = request()
    if history:
        payload.pop("after_cursor")
        payload.update(
            source_generation=initial["source_generation"], before_cursor=None, limit=64,
        )
    read = owner.observation_history if history else owner.observation_read
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read, payload)
        try:
            assert entered.wait(2), "point read did not reach its captured result"
            current = reactivate(owner, replacement)
        finally:
            finish.set()
        stale = future.result(timeout=3)
    assert stale["type"] == "error" and stale["error"]["code"] == "superseded"
    assert owner.observation_read(request()) == current
    assert owner.observation_status()["status"] == "ready"


@pytest.mark.parametrize("replacement", [False, True])
def test_stale_held_feed_cannot_retire_a_reactivated_admission(owner, monkeypatch, replacement):
    monkeypatch.setattr(owner, "_elected", lambda: True)
    enroll(owner)
    stream = owner.stream(wire.FEED_ACTION, request(), lambda: False)
    try:
        assert next(stream)["type"] == "snapshot"
        current = reactivate(owner, replacement)
        assert next(stream)["reason"] == "superseded"
        assert owner.observation_read(request()) == current
        assert owner.observation_status()["status"] == "ready"
    finally:
        stream.close()


@pytest.mark.parametrize("timing", ["initial-gate", "first-gate", "history-read"])
def test_held_feed_retries_publication_that_starts_after_its_condition_wait(
    owner, monkeypatch, timing,
):
    initial = enroll(owner)
    stream = owner.stream(wire.FEED_ACTION, request(), lambda: False)
    barrier = owner.observations.publication("project")
    state = {"injected": False, "held": False, "qualified": False}
    original_gate = owner.observations.gate
    original_delta = owner.observations.runtime.history.delta

    def begin():
        state["injected"] = state["held"] = True
        barrier.__enter__()

    def gate(*args, **kwargs):
        if timing in {"initial-gate", "first-gate"} and not state["injected"]:
            begin()
        try:
            result = original_gate(*args, **kwargs)
            state["qualified"] = True
            return result
        finally:
            if state["held"]:
                state["held"] = False
                barrier.__exit__(None, None, None)

    def delta(*args, **kwargs):
        if timing == "history-read" and state["qualified"] and not state["injected"]:
            begin()
        return original_delta(*args, **kwargs)

    try:
        if timing == "initial-gate":
            monkeypatch.setattr(owner.observations, "gate", gate)
        assert next(stream) == initial
        owner.publish("project", [row(title="advanced")], observed_at=2)
        monkeypatch.setattr(owner.observations, "gate", gate)
        monkeypatch.setattr(owner.observations.runtime.history, "delta", delta)
        update = next(stream)
        assert state["injected"], "publication did not overlap the intended provider gate"
        assert update["type"] == "delta"
        assert update["facts"]["row"]["title"] == "advanced"
        assert update["base_cursor"] == initial["cursor"]
        assert len(update["entries"]) == 1
        assert next(stream)["type"] == "heartbeat"
    finally:
        if state["held"]:
            barrier.__exit__(None, None, None)
        stream.close()
