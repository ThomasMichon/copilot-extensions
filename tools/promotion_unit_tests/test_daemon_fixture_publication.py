"""Actor-free daemon fixture admission waits for closed publication."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/tests/test_daemon_shell_lifetime_native.py"
FIXTURE = ROOT / "libs/agent-procutil/tests/fixture_daemon_instance_shell.ps1"


def ready_reader() -> Callable[[Path], dict[str, Any] | None]:
    node = next(
        node for node in ast.parse(SOURCE.read_bytes()).body
        if isinstance(node, ast.FunctionDef) and node.name == "_ready_report"
    )
    namespace: dict[str, Any] = {"Path": Path, "json": json}
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["_ready_report"]


def test_daemon_ready_name_does_not_admit_unclosed_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "ready.json").write_text('{"parent": {"pid": 42}}', encoding="utf-8")
    reader = ready_reader()
    calls = []

    def denied(path: Path, *args: Any, **kwargs: Any) -> str:
        calls.append(path)
        raise PermissionError("report must not be read before publication closes")

    monkeypatch.setattr(Path, "read_text", denied)
    assert reader(tmp_path) is None
    assert calls == []


def test_daemon_closed_publication_admits_actual_report(tmp_path: Path) -> None:
    report = {"parent": {"pid": 42, "birth": "windows:43"}, "helper": {"pid": 44}}
    (tmp_path / "ready.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "ready.closed").write_text("closed", encoding="ascii")
    assert ready_reader()(tmp_path) == report


@pytest.mark.parametrize("error", [
    PermissionError("literal admitted report denial"),
    json.JSONDecodeError("literal malformed report", "{", 0),
])
def test_daemon_admitted_read_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    (tmp_path / "ready.closed").write_text("closed", encoding="ascii")
    reader = ready_reader()
    calls = []

    def failed(path: Path, *args: Any, **kwargs: Any) -> str:
        calls.append(path)
        raise error

    monkeypatch.setattr(Path, "read_text", failed)
    with pytest.raises(type(error)) as caught:
        reader(tmp_path)
    assert caught.value is error
    assert calls == [tmp_path / "ready.json"]


def test_daemon_producer_closes_ready_before_publishing_admission() -> None:
    source = FIXTURE.read_text(encoding="utf-8")
    move = "[IO.File]::Move(($ready + '.pending'), $ready)"
    closed = "[IO.File]::WriteAllText((Join-Path $Root 'ready.closed'), 'closed')"
    assert source.count(move) == source.count(closed) == 1
    assert source.index(move) < source.index(closed)


def test_daemon_lifetime_wait_uses_actual_closed_publication_reader(tmp_path: Path) -> None:
    function = next(
        node for node in ast.parse(SOURCE.read_bytes()).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_original_shell_lifetime_without_compilation"
    )
    waits = [
        node for node in ast.walk(function) if isinstance(node, ast.While)
        and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "_ready_report" for call in ast.walk(node.test))
    ]
    assert len(waits) == 1
    assert "details := _ready_report(tmp_path)" in ast.unparse(waits[0].test)
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text" for node in ast.walk(function)
    )
    report = {"parent": {"pid": 42, "birth": "windows:43"}}
    (tmp_path / "ready.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "ready.closed").write_text("closed", encoding="ascii")
    assert ready_reader()(tmp_path) == report
