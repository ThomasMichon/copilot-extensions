"""Optional `hook.lifecycle.v1` journal for the sessionStart hook."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from process_telemetry import hook_journal
from process_telemetry.finite_owner import FiniteOwner

from test_consumer_telemetry_command import _admit, _run

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hook_telemetry = _load("hook_telemetry_under_test", _SCRIPTS / "hook_telemetry.py")
hook_client = _load("hook_client_telemetry_under_test", _SCRIPTS / "hook_client.py")


def _policy(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "consumer-telemetry.json").write_text(
        json.dumps({"schema_version": 1, "root": str(root)}), encoding="utf-8",
    )
    return root / "consumer-telemetry-hooks"


def test_without_admission_the_hook_writes_nothing(tmp_path: Path) -> None:
    assert hook_telemetry.begin(tmp_path) is None
    assert hook_telemetry.begin(None) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("error, expected", [
    (None, ("success", "none")),
    (RuntimeError("secret detail"), ("failure", "exception")),
])
def test_admitted_run_journals_one_content_free_record(
    tmp_path: Path, error: BaseException | None, expected: tuple[str, str],
) -> None:
    directory = _policy(tmp_path / "runtime")
    run = hook_telemetry.begin(tmp_path / "runtime")
    assert run is not None and any(directory.glob("*.start"))
    hook_telemetry.finish(run, error)
    (record,) = hook_journal.drain(directory)
    assert (record["plugin"], record["entry"]) == ("agent-worktrees", "sessionStart")
    assert (record["outcome"], record["failure_category"]) == expected
    assert "secret" not in json.dumps(record) and not any(directory.glob("*.start"))


def test_a_stopped_hook_is_reported_by_the_next_start(tmp_path: Path) -> None:
    directory = _policy(tmp_path / "runtime")
    stopped = hook_telemetry.begin(tmp_path / "runtime")
    assert stopped is not None
    later = hook_journal.HookRun.begin(directory, "agent-worktrees", "sessionStart", stale_after_ms=0)
    later.finish(outcome="success")
    outcomes = [(r["outcome"], r["failure_category"]) for r in hook_journal.drain(directory)]
    assert outcomes == [("unfinished", "unfinished_prior_run"), ("success", "none")]


def test_hook_client_session_start_journals_around_product_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "runtime"
    directory = _policy(runtime)
    seen: list[bool] = []
    monkeypatch.setattr(hook_client, "_selected_runtime_root", lambda home: runtime)
    monkeypatch.setattr(hook_client, "_managed_session_admitted", lambda *_: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"sessionId": "s"})))

    def enrich(value: dict[str, Any]) -> dict[str, Any]:
        seen.append(any(directory.glob("*.start")))
        raise RuntimeError("secret failure")

    monkeypatch.setattr(hook_client, "_enrich_session_payload", enrich)
    monkeypatch.setattr(hook_client, "_write_guidance_floor", lambda *_: None)
    assert hook_client.main(["sessionStart"]) == 0
    assert json.loads(capsys.readouterr().out) == {}
    assert seen == [True]
    (record,) = hook_journal.drain(directory)
    assert (record["outcome"], record["failure_category"]) == ("failure", "exception")


def test_finite_command_drains_the_journal_through_its_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _admit(tmp_path, monkeypatch)
    install = tmp_path / "install"
    hook_telemetry.finish(hook_telemetry.begin(install), None)
    offered: list[tuple[Any, dict[str, Any]]] = []
    emit = FiniteOwner.emit

    def spy(owner: Any, event: dict[str, Any]) -> None:
        offered.append((owner, event))
        emit(owner, event)

    # Observe the admitted offers rather than a receiver's timing-dependent readback.
    monkeypatch.setattr(FiniteOwner, "emit", spy)
    assert _run(monkeypatch, ["create"], lambda: 0) == 0
    assert [(o.source, o.source_schema) for o, _ in offered] == [
        ("agent-worktrees", "worktrees.lifecycle.v1"), ("agent-worktrees", "hook.lifecycle.v1"),
    ]
    hook_owner, event = offered[1]
    assert (event["kind"], event["outcome"]) == ("hook", "success")
    counters = hook_owner.status()["counters"]
    assert (counters["offered"], counters["dropped_invalid"]) == (1, 0)
    assert hook_journal.drain(install / "consumer-telemetry-hooks") == []
    assert str(tmp_path) not in json.dumps([e for _, e in offered])


@pytest.mark.parametrize("refusal", ["policy", "receiver"])
def test_refused_route_leaves_hook_records_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str,
) -> None:
    root = _admit(tmp_path, monkeypatch)
    install = tmp_path / "install"
    hook_telemetry.finish(hook_telemetry.begin(install), None)
    if refusal == "policy":
        (install / "consumer-telemetry.json").write_text(json.dumps({"schema_version": 9}), encoding="utf-8")
    else:
        # A valid owner policy whose receiver configuration refuses the route.
        (root / ".agent-telemetry" / "config.json").write_text(
            json.dumps({"schema_version": 1, "mode": "off"}), encoding="utf-8",
        )
    assert _run(monkeypatch, ["create"], lambda: 0) == 0
    assert len(hook_journal.drain(install / "consumer-telemetry-hooks")) == 1
