"""In-memory causal logging contracts; real file/daemon controls stay periodic."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(monkeypatch: pytest.MonkeyPatch, name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def diagnostics(monkeypatch: pytest.MonkeyPatch):
    # Load the actual pure diagnostic owner without importing unrelated native
    # process-launch machinery from the package's top-level convenience API.
    package = ModuleType("agent_procutil")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "agent_procutil", package)
    load(monkeypatch, "agent_procutil.diagnostics",
         "libs/agent-procutil/src/agent_procutil/diagnostics.py")
    module = load(
        monkeypatch, "promotion_dispatch_spawn_diagnostics",
        "plugins/agent-dispatch/src/agent_dispatch/spawn_diagnostics.py",
    )
    records: list[logging.LogRecord] = []
    monkeypatch.setattr(module.log, "info", lambda text, message, **kw: records.append(
        logging.makeLogRecord({"msg": text, "args": (message,), **kw["extra"]}),
    ))
    return module, records


def events(records: list[logging.LogRecord]) -> list[dict]:
    return [json.loads(record.getMessage()) for record in records]


def test_entry_precedes_work_and_return_preserves_correlation(diagnostics) -> None:
    module, records = diagnostics
    trace = module.SpawnDiagnostics(
        supervisor_id="lane-a", task_id="task-a", generation=3,
        reservation_key="reservation-a", creation_key="creation-a", cycle_id="cycle-a",
    )
    with trace.stage("prepare") as result:
        assert [e["outcome"] for e in events(records)] == ["entered"]
        assert module.current_inputs() is trace.secrets
        result["prepared"] = True
    assert module.current_inputs() == {}
    entered, returned = events(records)
    assert returned["prepared"] is True
    assert returned["outcome"] == "returned"
    assert entered["call_id"] == returned["call_id"]
    assert returned["elapsed_seconds"] >= 0
    assert returned["task_id"] == "task-a" and returned["creation_key"] == "creation-a"
    assert all(record.operation_id == "reservation-a" and record.correlation_id == "cycle-a"
               and record.generation == 3 for record in records)


def test_causal_exception_is_same_object_without_false_return(diagnostics, monkeypatch) -> None:
    module, records = diagnostics
    monkeypatch.setenv("UNIT_API_TOKEN", "synthetic-sensitive-value")
    error = ValueError("database busy allocation-a synthetic-sensitive-value")
    with pytest.raises(ValueError) as caught:
        with module.SpawnDiagnostics().stage("reserve"):
            raise error
    assert caught.value is error
    assert module.current_inputs() == {}
    entered, failed = events(records)
    assert failed["call_id"] == entered["call_id"]
    assert failed["outcome"] == "error" and failed["error_type"] == "ValueError"
    assert "database busy allocation-a" in failed["error"]
    assert "synthetic-sensitive-value" not in json.dumps(events(records))
    for message in ("[Errno 13] Permission denied: example-file",
                    "[WinError 2] The system cannot find example-file"):
        assert module.diagnostic_text(message, {}) == message
    for explicit in (False, True):
        cause = PermissionError(13, "permission allocation-a synthetic-sensitive-value")
        wrapper = RuntimeError("companion command unavailable")
        wrapper.__cause__ = cause if explicit else None
        wrapper.__context__ = None if explicit else cause
        wrapper.__suppress_context__ = explicit
        detail = module.exception_detail(wrapper, None, {})
        assert "RuntimeError: companion command unavailable" in detail
        assert "PermissionError: [Errno 13] permission allocation-a" in detail
        assert "synthetic-sensitive-value" not in detail
        assert ("caused by:" if explicit else "during handling:") in detail
        assert wrapper.__cause__ is (cause if explicit else None)
    wrapper.__cause__ = wrapper
    assert "chain cycle or limit" in module.exception_detail(wrapper, None, {})


def test_opaque_envelope_and_prompt_are_not_application_output(diagnostics) -> None:
    module, records = diagnostics
    secret = "synthetic private prompt"
    trace = module.SpawnDiagnostics(secrets={"STARTUP_PROMPT_SECRET": secret})
    with pytest.raises(ValueError):
        with trace.stage("bridge_send"):
            raise ValueError('{"error":"connection refused allocation-a","prompt":"' + secret + '"}')
    text = json.dumps(events(records))
    assert secret not in text
    assert "connection refused allocation-a" in text
    assert module.diagnostic_text('{"prompt":"broken', trace.secrets) == "<omitted:opaque-envelope>"
    long_prompt = secret * 5000
    assert module.diagnostic_text(
        "failure allocation-a " + long_prompt, {"STARTUP_PROMPT_SECRET": long_prompt},
    ) == "failure allocation-a [REDACTED]"
    assert module.diagnostic_fields(
        json.dumps({"error": "failure allocation-a " + long_prompt}),
        {"STARTUP_PROMPT_SECRET": long_prompt},
    ) == {"error": "failure allocation-a [REDACTED]"}
