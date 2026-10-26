"""Strict selected contexts, revision custody and exact runtime selection."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_worktrees import repository_continuity as continuity, repository_setup as setup
from test_repository_setup import URL, commit
from test_repository_setup import source as source


def context_source(path: Path, value: dict) -> setup.Setup:
    declaration = json.loads((path / setup.DECLARATION).read_text())
    declaration.update(version=2, contexts={"harness-a": value})
    (path / setup.DECLARATION).write_text(json.dumps(declaration))
    commit(path)
    return setup.inspect(path, URL)


def test_context_is_revision_bound_and_source_is_not_opened_on_inspection(source: Path) -> None:
    path = source.parent / "not-yet-provisioned.json"
    selected = context_source(source, {"provider": "local-projection-v1", "source": str(path)})
    assert continuity.selected(selected, "harness-a").source == str(path)
    assert not path.exists()
    selected.verify()
    with pytest.raises(setup.SetupError, match="not declared"):
        continuity.selected(selected, "absent")
    declaration = json.loads((source / setup.DECLARATION).read_text())
    declaration["contexts"]["harness-a"]["source"] = str(source.parent / "changed.json")
    (source / setup.DECLARATION).write_text(json.dumps(declaration))
    with pytest.raises(setup.SetupError):
        selected.verify()


@pytest.mark.parametrize("value", [
    {"provider": "unknown", "source": "/selected"},
    {"provider": "local-projection-v1", "source": "relative"},
    {"provider": "local-projection-v1", "source": 1},
    {"provider": "local-projection-v1", "source": "/selected", "extra": True},
])
def test_context_cannot_invent_provider_or_source_defaults(source: Path, value: dict) -> None:
    with pytest.raises(setup.SetupError):
        context_source(source, value)


def test_consumer_selects_its_engine_interpreter_with_bounded_native_custody(monkeypatch) -> None:
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"status":"ready","stage":"connected"}', "")
    monkeypatch.setattr(continuity, "run_caller_owned", run)
    assert continuity._invoke(["bootstrap"]) == {"status": "ready", "stage": "connected"}
    argv, options = calls[0]
    assert argv == [sys.executable, "-I", "-m", "fleet_manager", "bootstrap"]
    assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 16384
    assert options["timeout"] == 30
