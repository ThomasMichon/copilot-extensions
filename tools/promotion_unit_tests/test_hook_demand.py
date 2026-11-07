"""Authenticated response completion is bounded demand, not activation."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/src/agent_worktrees/hook_ipc.py"


@pytest.fixture
def api(monkeypatch):
    spec = importlib.util.spec_from_file_location("promotion_hook_demand", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    clock = {"wall": 100.0, "mono": 0.0}
    monkeypatch.setattr(module, "time", SimpleNamespace(
        time=lambda: clock["wall"],
        sleep=lambda *_a: pytest.fail("unit requested sleep"),
    ))
    owner = module.HookIpcServer.__new__(module.HookIpcServer)
    owner._active_handlers_lock = threading.Lock()
    owner._last_completed_hook = None
    owner._clock = lambda: clock["mono"]
    owner.generation = "a" * 32
    owner._read_demand_admitted = None
    owner._native_mutation_decide = None
    owner._worktree_changed = None
    owner._read_demand_lock = threading.RLock()

    def dispatch(change=None):
        request = {
            "version": 1, "token": "expected", "kind": "postToolUse",
            "payload": {"toolName": "view"}, "deadline": 101.0,
        }
        server = SimpleNamespace(
            token="expected", decide=lambda *a: {"additionalContext": "handled"},
            owner=owner, admission_closed=False, admission_closed_reason=None,
        )
        owner.server = server
        handler = module._Handler.__new__(module._Handler)
        handler.request = SimpleNamespace(settimeout=lambda value: None)
        handler.wfile = io.BytesIO()
        if change:
            change(request, server, handler)
        handler.server = server
        handler.rfile = io.BytesIO((json.dumps(request) + "\n").encode())
        handler.handle()
        raw = handler.wfile.getvalue()
        return json.loads(raw) if raw else None

    return SimpleNamespace(module=module, clock=clock, owner=owner, dispatch=dispatch)


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse"])
def test_completed_hook_response_retains_demand_until_monotonic_expiry(api, kind):
    assert not api.owner.has_recent_hook_demand()
    response = api.dispatch(lambda request, *_a: request.update(kind=kind))
    assert response["version"] == 1
    assert response["result"] == {"additionalContext": "handled"}
    assert api.owner.has_recent_hook_demand()
    api.clock.update(wall=-1000000.0, mono=119.0)
    assert api.owner.has_recent_hook_demand()
    api.clock["mono"] = 120.0
    assert not api.owner.has_recent_hook_demand()


@pytest.mark.parametrize("case", [
    "current", "generation", "closed", "revoked", "legacy", "deadline", "extra", "version",
])
def test_send_only_read_demand_keeps_original_admission_and_expiry(api, case):
    def change(request, server, handler):
        request.clear()
        request.update(
            version=1, token="expected", kind="readHookDemand",
            hook_generation=api.owner.generation, deadline=101.0,
        )
        api.owner._read_demand_admitted = lambda: True
        server.decide = lambda *_a: pytest.fail("read demand invoked policy")
        if case == "generation":
            request["hook_generation"] = "b" * 32
        elif case == "closed":
            server.admission_closed = True
        elif case == "revoked":
            api.owner._read_demand_admitted = lambda: False
        elif case == "legacy":
            api.owner._read_demand_admitted = None
        elif case == "deadline":
            request["deadline"] = float("nan")
        elif case == "extra":
            request["payload"] = {"toolName": "edit"}
        elif case == "version":
            request["version"] = True

    assert api.dispatch(change) is None
    assert api.owner.has_recent_hook_demand() is (case == "current")
    api.clock["mono"] = 120.0
    assert not api.owner.has_recent_hook_demand()


@pytest.mark.parametrize("case", [
    "current", "generation", "closed", "unavailable", "legacy",
    "oversized", "version", "deadline", "payload", "declined", "revoked", "contended",
])
def test_native_mutation_is_bounded_send_only_and_never_replies_on_refusal(api, case):
    _assert_native_mutation_boundary(api, case, "nativeToolMutation")


@pytest.mark.parametrize("case", [
    "current", "generation", "closed", "revoked", "legacy", "deadline", "payload", "version",
    "contended", "expired-after-lock",
])
def test_worktree_change_is_optional_send_only_and_never_invokes_policy(api, case):
    seen = []
    cwd = str(SOURCE.parent)

    def change(request, server, handler):
        request.clear()
        request.update(
            version=1, token="expected", kind="worktreeChanged",
            hook_generation=api.owner.generation, deadline=101.0, payload={"cwd": cwd},
        )
        api.owner._worktree_changed = seen.append
        server.decide = lambda *_a: pytest.fail("worktree change invoked policy")
        if case == "generation":
            request["hook_generation"] = "b" * 32
        elif case == "closed":
            server.admission_closed = True
        elif case == "revoked":
            api.owner._read_demand_admitted = lambda: False
        elif case == "legacy":
            api.owner._worktree_changed = None
        elif case == "deadline":
            request["deadline"] = float("nan")
        elif case == "payload":
            request["payload"]["cwd"] = "relative-worktree"
        elif case == "version":
            request["version"] = True
        elif case == "contended":
            api.owner._read_demand_lock = SimpleNamespace(
                acquire=lambda **kwargs: False,
                release=lambda: pytest.fail("unowned worktree-change lock release"),
            )
        elif case == "expired-after-lock":
            def acquire(**kwargs):
                api.clock["wall"] = 101.0
                return True
            api.owner._read_demand_lock = SimpleNamespace(acquire=acquire, release=lambda: None)

    assert api.dispatch(change) is None
    assert seen == ([cwd] if case == "current" else [])
    assert api.owner.has_recent_hook_demand() is (case == "current")


@pytest.mark.parametrize("case", [
    "current", "generation", "closed", "unavailable", "legacy",
    "oversized", "version", "deadline", "payload", "declined", "revoked", "contended",
])
def test_native_batch_mutation_uses_original_send_only_boundary(api, case):
    _assert_native_mutation_boundary(api, case, "nativeToolBatchMutation")


@pytest.mark.parametrize("case", [
    "current", "generation", "closed", "unavailable", "legacy",
    "oversized", "version", "deadline", "payload", "declined", "revoked", "contended",
])
def test_shell_mutation_uses_original_send_only_boundary(api, case):
    _assert_native_mutation_boundary(api, case, "shellToolMutation")


@pytest.mark.parametrize("case", [
    "current", "generation", "closed", "unavailable", "legacy",
    "oversized", "version", "deadline", "payload", "declined", "revoked", "contended",
])
def test_mixed_batch_mutation_uses_original_send_only_boundary(api, case):
    _assert_native_mutation_boundary(api, case, "mixedToolBatchMutation")


def _assert_native_mutation_boundary(api, case, kind):
    seen = []
    payload = {"toolName": "multi_edit", "toolArgs": {"edits": [{"path": "one"}]}}
    if kind == "nativeToolBatchMutation":
        payload = {"toolCalls": [{"name": "multi_edit", "args": payload["toolArgs"]}]}
    elif kind == "shellToolMutation":
        payload = {"toolName": "powershell", "toolArgs": '{"command":"git pull --ff-only"}'}
    elif kind == "mixedToolBatchMutation":
        payload = {"toolCalls": [
            {"name": "multi_edit", "args": payload["toolArgs"]},
            {"name": "powershell", "args": '{"command":"git pull --ff-only"}'},
        ]}

    def decide(*args):
        if case == "unavailable":
            raise api.module.HookUnavailable
        if case == "declined":
            return None
        seen.append(args)
        return {}

    def change(request, server, handler):
        request.clear()
        request.update(
            version=1, token="expected", kind=kind,
            hook_generation=api.owner.generation, deadline=101.0,
            payload=payload,
        )
        api.owner._native_mutation_decide = decide
        server.decide = lambda *_a: pytest.fail("notification invoked response policy")
        if case == "generation":
            request["hook_generation"] = "b" * 32
        elif case == "closed":
            server.admission_closed = True
        elif case == "legacy":
            api.owner._native_mutation_decide = None
        elif case == "oversized":
            request["payload"]["padding"] = "x" * 65536
        elif case == "version":
            request["version"] = True
        elif case == "deadline":
            request["deadline"] = float("nan")
        elif case == "payload":
            request["payload"] = []
        elif case == "revoked":
            api.owner._read_demand_admitted = lambda: False
        elif case == "contended":
            api.owner._read_demand_lock = SimpleNamespace(
                acquire=lambda *, timeout: False,
                release=lambda: pytest.fail("unowned lock release"),
            )

    assert api.dispatch(change) is None
    assert bool(seen) is (case == "current")
    assert api.owner.has_recent_hook_demand() is (case == "current")
    if seen:
        assert seen[0][:2] == (kind, payload)


@pytest.mark.parametrize("case", [
    "token", "version", "payload", "kind", "expired", "closed",
    "failed", "unavailable", "late", "result", "serialization", "write",
])
def test_rejected_or_uncompleted_requests_do_not_retain_hook_demand(api, case):
    def change(request, server, handler):
        if case in {"token", "version", "payload", "kind", "expired"}:
            request.update({
                "token": {"token": "foreign"},
                "version": {"version": 2},
                "payload": {"payload": []},
                "kind": {"kind": "handoffWake"},
                "expired": {"deadline": 99.0},
            }[case])
        elif case == "closed":
            server.admission_closed = True
            server.admission_closed_reason = "superseded"
        elif case in {"failed", "unavailable"}:
            def refuse(*args):
                raise api.module.HookUnavailable if case == "unavailable" else RuntimeError()
            server.decide = refuse
        elif case == "late":
            def late(*args):
                api.clock["wall"] = 101.0
                return {}
            server.decide = late
        elif case == "result":
            server.decide = lambda *a: "not a decision"
        elif case == "serialization":
            server.decide = lambda *a: {"not-json": object()}
        else:
            class Broken(io.BytesIO):
                def write(self, data):
                    raise BrokenPipeError
            handler.wfile = Broken()

    api.dispatch(change)
    assert not api.owner.has_recent_hook_demand()


@pytest.fixture
def exit_api(monkeypatch):
    events = []

    def record(directory, action, **kwargs):
        events.append({"action": action, **kwargs})
        return events[-1]

    lifecycle = SimpleNamespace(record=record, BEGIN="begin", OK="ok", FAIL="fail")
    monkeypatch.setitem(sys.modules, "zdd", SimpleNamespace(lifecycle=lifecycle))
    source = SOURCE.with_name("status_monitor_drain.py")
    spec = importlib.util.spec_from_file_location("promotion_monitor_exit", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return SimpleNamespace(
        module=module, trace=module.MonitorExitTrace(Path("unused"), 12345), events=events,
    )


def test_idle_diagnostics_preserve_short_circuit_and_do_not_write_during_checks(exit_api):
    trace = exit_api.trace
    probes = []

    def probe(name, clear):
        probes.append(name)
        return clear

    assert not (
        trace.begin_idle()
        and trace.check("active-route", probe("route", True))
        and trace.check("picker", probe("picker", False))
        and trace.check("list", probe("must-not-run", True))
    )
    assert probes == ["route", "picker"] and exit_api.events == []
    with trace.retiring("shutdown-requested", None):
        assert len(exit_api.events) == 1
    assert [event["detail"] for event in exit_api.events] == [
        {"phase": phase, "reason": "shutdown-requested",
         "last_idle_eligibility": {"active-route": True, "picker": False}}
        for phase in ("cleanup-begin", "cleanup-complete")
    ]


@pytest.mark.parametrize(
    "error", [RuntimeError("private message"), KeyboardInterrupt()], ids=["error", "interrupt"],
)
def test_retirement_diagnostics_preserve_original_error_without_recording_it(exit_api, error):
    with pytest.raises(type(error)) as raised:
        with exit_api.trace.retiring("idle-no-demand", error):
            raise error
    assert raised.value is error
    assert [event["outcome"] for event in exit_api.events] == ["begin", "fail"]
    reason = "interrupted" if isinstance(error, KeyboardInterrupt) else "loop-error"
    assert exit_api.events[-1]["detail"] == {
        "phase": "cleanup-failed", "reason": reason, "last_idle_eligibility": {},
    }


def test_idle_diagnostics_clear_prior_observation_and_filter_unknown_labels(exit_api):
    trace = exit_api.trace
    trace.check("picker", False)
    trace.begin_idle()
    assert trace.check("private input", True)
    with trace.retiring("private reason", None):
        pass
    assert exit_api.events[-1]["detail"] == {
        "phase": "cleanup-complete", "reason": "loop-error", "last_idle_eligibility": {},
    }


def test_unavailable_exit_log_does_not_prevent_retirement(exit_api, monkeypatch):
    monkeypatch.setattr(exit_api.module.lifecycle, "record", lambda *a, **k: None)
    completed = []
    with exit_api.trace.retiring("idle-no-demand", None):
        completed.append(True)
    assert completed == [True] and exit_api.events == []


@pytest.mark.parametrize("phase", ["cleanup-begin", "cleanup-failed", "cleanup-complete"])
def test_log_interruption_cannot_skip_cleanup_or_replace_its_error(exit_api, monkeypatch, phase):
    record = exit_api.module.lifecycle.record

    def interrupt(directory, action, **kwargs):
        if kwargs["detail"]["phase"] == phase:
            raise KeyboardInterrupt
        return record(directory, action, **kwargs)

    monkeypatch.setattr(exit_api.module.lifecycle, "record", interrupt)
    error = RuntimeError("original retirement error") if phase == "cleanup-failed" else None
    completed, caught = [], None
    try:
        with exit_api.trace.retiring("idle-no-demand", error):
            completed.append(True)
            if error is not None:
                raise error
    except BaseException as exc:
        caught = exc
    assert completed == [True]
    assert caught is error
