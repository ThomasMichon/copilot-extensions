"""Actor-free admission and ownership controls for cell dependency execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

_source = Path(__file__).with_name("test_index_cell_probes.py")
_spec = importlib.util.spec_from_file_location("_cell_dependency_units_base", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
cell = cast(Any, _module).cell


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_dependency_admits_selected_interpreter_before_native_execution(
    cell: Any, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    helper = cell._cell_process_helpers()
    root = Path("fixture-cell").absolute()
    interpreter = root / "application"
    uv = str(Path("private-tools/uv").absolute())
    events: list[tuple[str, Any, Any]] = []
    environment = {"UV_CONFIG_FILE": "retained-resolver-settings"}

    def select(expected: Path, **options: Any) -> str:
        events.append(("admit", expected, options))
        return str(expected)

    monkeypatch.setattr(helper, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(Path, "is_file", lambda *a: True)
    monkeypatch.setattr(helper.shutil, "which", lambda *a: "private-tools/shell")
    monkeypatch.setattr(
        helper, "_prepared_process_helpers",
        lambda: (SimpleNamespace(resolve_runtime_python=select), SimpleNamespace()),
    )
    helper.install_dependency(
        root, "private-tools/uv", interpreter, "source-package", root / "snapshot",
        error_type=cell.CellError, validate_file=lambda *a: None, quote=repr,
        isolated_environment=lambda: environment,
        execute=lambda command, **options: events.append(("execute", command, options)),
    )
    assert events[0] == ("admit", interpreter, {"uv": uv})
    assert len(events) == 2 and events[1][0] == "execute"
    command, options = events[1][1:]
    assert command[0] == str(Path("private-tools/shell").absolute())
    assert options == {"environment": environment, "cwd": root / "snapshot"}
    assert options["environment"] is environment
    assert environment == {"UV_CONFIG_FILE": "retained-resolver-settings"}
    if platform == "posix":
        assert command[5:] == [
            uv, "--python", str(interpreter), "source-package", "--quiet",
        ]
    else:
        assert repr(uv) in command[4] and repr(str(interpreter)) in command[4]


@pytest.mark.parametrize("outcome", ["ordinary", "missing-helper", "uncertain"])
def test_dependency_admission_failure_preserves_custody(
    cell: Any, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    helper = cell._cell_process_helpers()
    _, owner = helper._prepared_process_helpers()
    process, job, resource = object(), object(), object()
    failure: Any = (
        owner.OwnedCleanupError(process, job, cleanup_resources=(("fixture", resource),))
        if outcome == "uncertain" else
        FileNotFoundError("missing private helper") if outcome == "missing-helper" else
        RuntimeError("private admission output")
    )
    root = Path("fixture-cell").absolute()

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise failure

    def prepared() -> tuple[Any, Any]:
        if outcome == "missing-helper":
            raise failure
        return SimpleNamespace(resolve_runtime_python=refuse), owner

    monkeypatch.setattr(Path, "is_file", lambda *a: True)
    monkeypatch.setattr(helper.shutil, "which", lambda *a: "selected-shell")
    monkeypatch.setattr(helper, "_prepared_process_helpers", prepared)
    with pytest.raises(owner.OwnedCleanupError if outcome == "uncertain" else cell.CellError) as caught:
        helper.install_dependency(
            root, str(root / "uv"), root / "python", "source", root,
            error_type=cell.CellError, validate_file=lambda *a: None, quote=repr,
            isolated_environment=lambda: {},
            execute=lambda *a, **k: pytest.fail("native execution followed failed admission"),
        )
    if outcome == "uncertain":
        assert caught.value is failure
        assert failure.process is process and failure.job is job
        assert failure.cleanup_resources == (("fixture", resource),)
    else:
        assert caught.value.__cause__ is failure
        assert type(failure).__name__ in str(caught.value)
        assert "private" not in str(caught.value)


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("missing", ["engine", "shell"])
def test_dependency_prerequisites_refuse_before_admission(
    cell: Any, monkeypatch: pytest.MonkeyPatch, platform: str, missing: str,
) -> None:
    helper = cell._cell_process_helpers()
    root = Path("fixture-cell").absolute()
    monkeypatch.setattr(helper, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(Path, "is_file", lambda *a: True)
    monkeypatch.setattr(helper.shutil, "which", lambda *a: None)
    monkeypatch.setattr(
        helper, "_prepared_process_helpers",
        lambda: pytest.fail("admission ran without native prerequisites"),
    )

    def validate(*args: Any) -> None:
        if missing == "engine":
            raise cell.CellError("missing canonical engine")

    with pytest.raises(cell.CellError):
        helper.install_dependency(
            root, str(root / "uv"), root / "python", "source", root,
            error_type=cell.CellError, validate_file=validate, quote=repr,
            isolated_environment=lambda: {},
            execute=lambda *a, **k: pytest.fail("native execution lacked prerequisites"),
        )
