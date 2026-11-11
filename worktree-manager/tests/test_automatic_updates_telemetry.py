"""Published telemetry vocabulary at the real automatic-update boundary."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from zdd.cutover_lock import CutoverLock

from worktree_manager import __main__ as manager
from worktree_manager import automatic_updates_cli as cli
from worktree_manager import automatic_updates_policy as policy


ORIGINS = (None, "manager", "external", "legacy-agent-machines")
SCHEMAS = Path(__file__).resolve().parents[2] / "libs" / "update-telemetry" / "schemas"


def _arguments(origin: str | None) -> list[str]:
    if origin is None:
        return []
    arguments = ["--automatic-trigger", origin]
    return arguments + (["--controller", "example-controller"] if origin == "external" else [])


def _assert_status(telemetry: Path, origin: str | None, outcome: str) -> dict:
    status = json.loads((telemetry / "update-status.json").read_text(encoding="utf-8"))
    schema = json.loads((SCHEMAS / "update-status.schema.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) <= status.keys()
    run_schema = schema["properties"]["run"]
    assert set(run_schema["required"]) <= status["run"].keys()
    trigger = status["run"]["trigger"]
    assert trigger in run_schema["properties"]["trigger"]["enum"]
    assert trigger == ("schedule" if origin is not None else "manual")
    assert status["run"]["outcome"] == outcome
    assert status["run"]["event_parse_errors"] == 0
    return status


def _assert_events(status: dict, origin: str | None) -> list[dict]:
    schema = json.loads((SCHEMAS / "update-event.schema.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in Path(status["run"]["events_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert events
    for event in events:
        assert set(schema["required"]) <= event.keys()
        assert schema["additionalProperties"] is False
        assert event.keys() <= schema["properties"].keys()
        for key, definition in schema["properties"].items():
            if key in event and "enum" in definition:
                assert event[key] in definition["enum"], (key, event[key])
    origins = [event for event in events if event.get("action") == "automatic_trigger"]
    if origin is None:
        assert not origins
    else:
        assert len(origins) == 1
        assert origins[0]["phase"] == "run"
        assert origins[0]["event"] == "info"
        assert origins[0]["details"] == {"automatic_trigger": origin}
    return events


@pytest.fixture
def telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "telemetry"
    monkeypatch.setattr(cli, "default_root", lambda: tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_UPDATE_HOME", str(home))
    for key in ("COPILOT_EXTENSIONS_UPDATE_RUN_ID", "COPILOT_EXTENSIONS_UPDATE_EVENTS",
                "COPILOT_EXTENSIONS_UPDATE_OWNER"):
        monkeypatch.delenv(key, raising=False)
    return home


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.parametrize("code,outcome", [(0, "verified_current"), (4, "auth_required")])
def test_admitted_runs_publish_canonical_trigger_and_separate_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, telemetry: Path,
    origin: str | None, targeted: bool, code: int, outcome: str,
) -> None:
    from worktree_manager import engine_client, runtime_source_update
    from worktree_manager import update_bootstrap, update_orchestration

    if origin == "manager":
        policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    elif origin == "external":
        policy.configure(
            tmp_path, owner="external", provenance="user", external_controller="example-controller",
        )
    monkeypatch.setattr(update_orchestration, "preflight_runtime_source", lambda *a, **k: True)
    monkeypatch.setattr(runtime_source_update, "repair_selection", lambda args: nullcontext(None))
    monkeypatch.setattr(update_bootstrap, "prepare_update", lambda args: SimpleNamespace(
        exit_code=0, arguments=args, fresh_manager=None, environment={},
    ))
    calls = []

    def execute(*args, **kwargs) -> int:
        calls.append((args, kwargs))
        _assert_status(telemetry, origin, "in_progress")
        return code

    monkeypatch.setattr(manager, "_cmd_update_steps", execute)
    monkeypatch.setattr(engine_client, "run_engine_passthrough", execute)
    arguments = _arguments(origin) + (["--target", "agent-example"] if targeted else [])
    assert manager._cmd_update(arguments) == code
    assert len(calls) == 1
    status = _assert_status(telemetry, origin, outcome)
    _assert_events(status, origin)


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("locked", [False, True])
def test_admission_refusal_publishes_only_shared_vocabulary(
    tmp_path: Path, telemetry: Path, origin: str | None, locked: bool,
) -> None:
    if not locked and origin in (None, "legacy-agent-machines"):
        policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    arguments = _arguments(origin)
    if not locked and origin is None:
        arguments = ["--controller", "unexpected-controller"]
    guard = CutoverLock(policy.state_dir(tmp_path) / "update-transaction") if locked else nullcontext()

    def forbidden(*args) -> int:
        pytest.fail("refused update reached installation work")

    with guard:
        assert cli.guarded_update(arguments, forbidden) == (5 if locked else 3)
    status = _assert_status(
        telemetry, origin, "blocked_by_lock" if locked else "configuration_limitation",
    )
    events = _assert_events(status, origin)
    admission = [event for event in events if event.get("action") == "update_admission"]
    assert len(admission) == 1
    assert admission[0]["phase"] == "lock"
    assert admission[0]["error"]["code"] == (
        "update_transaction_held" if locked else "automatic_policy_refused"
    )
