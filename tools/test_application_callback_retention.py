"""The real child owner must not clamp an opted-in callback to acquisition time."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
for directory in ("installer-engine", "versioned-runtime"):
    sys.path.insert(0, str(ROOT / "libs" / directory))
import installer_engine
from test_local_install_source import application_adapter as _application_adapter

application_adapter = _application_adapter
_CHILD = installer_engine._child


def test_long_callback_reaches_real_child_owner_with_retained_execution_budget(
    application_adapter: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = application_adapter
    source, backend = fixture.source, fixture.source.engine
    clock = [100.0]
    monkeypatch.setattr(source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(backend, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    for location in (fixture.application, fixture.old):
        path = location / "application.json"
        declaration = json.loads(path.read_text())
        declaration["callback_timeout_seconds"] = {"setup": 180, "start": 900}
        path.write_text(json.dumps(declaration))

    def check_deadline(deadline: float) -> None:
        if clock[0] >= deadline:
            raise TimeoutError("retained execution deadline expired")

    def verify() -> None:
        check_deadline(fixture.inputs.deadline)

    @contextmanager
    def retained(
        snapshot: Path, component: str, *, retention_deadline: float | None = None,
    ) -> Iterator[tuple[Any, SimpleNamespace]]:
        fixture.inputs.deadline = retention_deadline or clock[0] + 600
        fixture.inputs.verify = verify
        yield source.select_project(snapshot, component), fixture.inputs
        verify()

    observations: list[dict[str, Any]] = []

    def run_owned(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        observations.append(options)
        assert options["timeout"] == 1740
        assert options["deadline"] == 3040
        clock[0] += 901
        check_deadline(options["deadline"])
        (fixture.old / "install-success.json").write_text(json.dumps({
            "state": "installed", "version": fixture.old.name,
        }))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(source, "project_inputs", retained)
    monkeypatch.setattr(backend, "_child", _CHILD)
    monkeypatch.setattr(backend, "_check_deadline", check_deadline)
    monkeypatch.setattr(backend, "_procutil", lambda _inputs: ModuleType("fixture_procutil"))
    monkeypatch.setattr(backend, "run_owned", run_owned)
    result = fixture.install()
    assert result == {"state": "installed", "slot": str(fixture.old)}
    assert clock[0] == 1001
    assert len(observations) == 1
