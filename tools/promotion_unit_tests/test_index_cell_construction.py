"""Actor-free cell-constructor routing, reuse and prerequisite controls."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

_source = Path(__file__).with_name("test_index_cell_probes.py")
_spec = importlib.util.spec_from_file_location("_cell_construction_units_base", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
cell = cast(Any, _module).cell


@pytest.mark.parametrize("outcome", ["create", "reuse", "failed"])
def test_cell_builder_retains_constructor_policy_and_owner(
    cell: Any, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    root = Path("fixture-cell").absolute()
    snapshot, policy, slot = root / "snapshot", root / "policy", root / "slot"
    python, uv = cell._venv_python(slot), "selected-uv"
    helper = cell._cell_process_helpers()
    constructions = []
    dependencies = []
    failure = cell.CellError("constructor failure")

    def construct(*args: Any, **kwargs: Any) -> None:
        constructions.append((args, kwargs))
        if outcome == "failed":
            raise failure

    monkeypatch.delenv("AGENT_INDEX_CELL_BUILD_SMOKE", raising=False)
    monkeypatch.setattr(cell, "shutil", SimpleNamespace(which=lambda *a: uv), raising=False)
    for name in (
        "_assert_directory", "_assert_path_chain", "_assert_not_reparse",
        "_assert_regular_file", "_validate_runtime_interpreter_layout", "_run_checked",
    ):
        monkeypatch.setattr(cell, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cell, "_lexists", lambda *a: False, raising=False)
    monkeypatch.setattr(cell, "_powershell_quote", str, raising=False)
    monkeypatch.setattr(
        Path, "exists", lambda path: outcome == "reuse" and path in (slot, python),
    )
    monkeypatch.setattr(helper, "construct_runtime", construct)
    monkeypatch.setattr(
        cell, "_install_runtime_dependency", lambda *a: dependencies.append(a), raising=False,
    )
    monkeypatch.setattr(cell, "_runtime_module_path", lambda *a, **k: snapshot, raising=False)

    def build() -> Path:
        return cast(Path, cell._build_runtime(
            snapshot, slot, marketplace_id="example", runtime_version="1.0", role=None,
            policy_payload_root=policy,
        ))

    if outcome == "failed":
        with pytest.raises(cell.CellError) as caught:
            build()
        assert caught.value is failure
        assert not dependencies
    else:
        assert build() == python
        assert len(dependencies) == 3
    assert len(constructions) == (0 if outcome == "reuse" else 1)
    if constructions:
        args, kwargs = constructions[0]
        assert args == (policy, str(Path(uv).absolute()), slot, snapshot)
        assert kwargs["python_version"] == "3.11"
        assert kwargs["execute"] is cell._run_checked
        assert kwargs["validate_file"] is cell._assert_regular_file
        assert kwargs["error_type"] is cell.CellError


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("missing", ["uv", "engine", "bootstrap", "shell"])
def test_constructor_refuses_before_native_execution(
    cell: Any, monkeypatch: pytest.MonkeyPatch, platform: str, missing: str,
) -> None:
    helper = cell._cell_process_helpers()
    root = Path("fixture-cell").absolute()
    uv = root / ("uv.exe" if platform == "nt" else "uv")
    monkeypatch.setattr(
        helper, "os", SimpleNamespace(name=platform, access=lambda *a: True, X_OK=os.X_OK),
    )
    monkeypatch.setattr(Path, "is_file", lambda path: not (missing == "uv" and path == uv))
    monkeypatch.setattr(helper.shutil, "which", lambda *a: None if missing == "shell" else "shell")
    checked = []

    def validate(_path: Path, label: str) -> None:
        checked.append(label)
        if missing in {"engine", "bootstrap"} and label.endswith(missing):
            raise cell.CellError("missing fixture " + missing)

    with pytest.raises(cell.CellError):
        helper.construct_runtime(
            root, str(uv), root / "slot", root, python_version="3.11",
            error_type=cell.CellError, validate_file=validate, quote=str,
            isolated_environment=lambda: {},
            execute=lambda *a, **k: pytest.fail("native constructor started without prerequisites"),
        )
    if missing == "uv":
        assert not checked


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_constructor_retains_callback_environment_and_cwd(
    cell: Any, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    helper = cell._cell_process_helpers()
    root = Path("fixture-cell").absolute()
    uv = root / ("uv.exe" if platform == "nt" else "uv")
    environment = {"retained": "caller configuration"}
    calls = []
    monkeypatch.setattr(
        helper, "os", SimpleNamespace(name=platform, access=lambda *a: True, X_OK=os.X_OK),
    )
    monkeypatch.setattr(Path, "is_file", lambda *a: True)
    monkeypatch.setattr(helper.shutil, "which", lambda *a: "selected-shell")
    helper.construct_runtime(
        root, str(uv), root / "slot", root / "snapshot", python_version="3.10.20",
        error_type=cell.CellError, validate_file=lambda *a: None, quote=str,
        isolated_environment=lambda: environment,
        execute=lambda *a, **k: calls.append((a, k)),
    )
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][0] == str(Path("selected-shell").absolute())
    assert kwargs["environment"] is environment
    assert kwargs["cwd"] == root / "snapshot"
    assert environment == {"retained": "caller configuration"}
