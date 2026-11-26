"""Actor-free admission and failure-custody controls for cell probes."""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cell(monkeypatch: pytest.MonkeyPatch) -> Any:
    source = ROOT / "plugins/agent-index/scripts/cell-runtime.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    selected = {
        "CellError", "_venv_python", "_cell_process_helpers", "_runtime_install_target",
        "_build_runtime", "_provision_constructed", "_isolated_environment",
        "_resume_selection_transaction",
    }
    definitions: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in selected
        or isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id in {"PLUGIN_ID", "RUNTIME_PROFILE_FILE", "_process_helpers"}
            for target in node.targets
        )
        or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == "_process_helpers"
    ]
    assert {node.name for node in definitions if hasattr(node, "name")} == selected
    module = ModuleType("_unit_cell_probes")
    module.__dict__.update(
        __file__=str(source), Path=Path, os=os, importlib=importlib, hashlib=hashlib, cast=cast,
        _context_arguments=lambda *a, **k: [],
    )
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"), module.__dict__)
    monkeypatch.syspath_prepend(str(ROOT / "libs/agent-procutil/src"))
    from agent_procutil import managed_python, owned_process
    monkeypatch.setattr(
        module._cell_process_helpers(), "_prepared_process_helpers",
        lambda: (managed_python, owned_process),
    )
    return module


@pytest.mark.parametrize("phase", ["lookup", "probe"])
@pytest.mark.parametrize("outcome", ["ordinary", "timeout", "output-limit", "invalid-utf8", "uncertain"])
def test_probe_failure_preserves_custody(
    cell: Any, monkeypatch: pytest.MonkeyPatch,
    phase: str, outcome: str,
) -> None:
    helper = cell._cell_process_helpers()
    root = Path("fixture-runtime").absolute()
    _, owner = helper._prepared_process_helpers()
    process, job, resource = object(), object(), object()
    error: Any
    if outcome == "uncertain":
        error = owner.OwnedCleanupError(process, job, cleanup_resources=(("fixture", resource),))
    elif outcome == "timeout":
        error = subprocess.TimeoutExpired(
            "private child command", 10, output="private child output", stderr="private child stderr",
        )
    elif outcome == "output-limit":
        error = owner.OutputLimitExceeded("stdout", 65536)
    elif outcome == "invalid-utf8":
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid fixture byte")
    else:
        error = RuntimeError("private child refusal")
    calls = []

    def select(_python: Path) -> str:
        calls.append("lookup")
        if phase == "lookup":
            raise error
        return str(root / "python")

    def capture(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("probe")
        raise error

    monkeypatch.setattr(helper, "_prepared_process_helpers", lambda: (
        SimpleNamespace(resolve_runtime_python=select),
        SimpleNamespace(run_caller_owned=capture, OwnedCleanupError=owner.OwnedCleanupError),
    ))
    with pytest.raises(owner.OwnedCleanupError if outcome == "uncertain" else cell.CellError) as caught:
        helper.runtime_probe(
            root / "python", root, "raise AssertionError",
            environment={}, error_type=cell.CellError,
            isolated_environment=helper.isolated_environment,
        )
    assert calls == (["lookup"] if phase == "lookup" else ["lookup", "probe"])
    if outcome == "uncertain":
        assert caught.value is error
        assert error.process is process and error.job is job
        assert error.cleanup_resources == (("fixture", resource),)
    else:
        assert caught.value.__cause__ is error
        assert type(error).__name__ in str(caught.value)
        assert "private child" not in str(caught.value)
        assert "\\xff" not in str(caught.value)


def test_smoke_site_probe_uses_shared_admission(
    cell: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, slot = Path("fixture-snapshot"), Path("fixture-slot")
    python = cell._venv_python(slot)
    calls = []

    def probe(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 17, "", "inert failure")

    monkeypatch.setenv("AGENT_INDEX_CELL_BUILD_SMOKE", "1")
    for name in ("_assert_directory", "_assert_path_chain", "_assert_not_reparse"):
        monkeypatch.setattr(cell, name, lambda *a: None, raising=False)
    monkeypatch.setattr(cell, "_lexists", lambda *a: False, raising=False)
    monkeypatch.setattr(Path, "exists", lambda path: path == python)
    monkeypatch.setattr(
        cell, "_validate_runtime_interpreter_layout", lambda *a, **k: None, raising=False,
    )
    monkeypatch.setattr(cell._cell_process_helpers(), "runtime_probe", probe)
    with pytest.raises(cell.CellError, match="could not resolve site-packages"):
        cell._build_runtime(
            snapshot, slot, marketplace_id="example", runtime_version="1.0", role=None,
        )
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == (python, slot)
    assert "site.getsitepackages()" in args[2]
    assert kwargs["error_type"] is cell.CellError


@pytest.mark.parametrize("uncertain", [False, True])
def test_candidate_discard_requires_settled_probe(
    cell: Any, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    helper = cell._cell_process_helpers()
    _, owner = helper._prepared_process_helpers()
    process, job, resource = object(), object(), object()
    error = (
        owner.OwnedCleanupError(process, job, cleanup_resources=(("fixture", resource),))
        if uncertain else cell.CellError("settled probe failure")
    )
    root = Path("fixture-runtime")
    slot = root / "versions" / "1.0"
    discarded = []
    provisioned = []
    context_owner = SimpleNamespace(
        discard_failed_runtime_slot=lambda **kwargs: discarded.append(kwargs),
    )
    monkeypatch.setattr(cell, "_cell_environment", lambda *a: {}, raising=False)
    monkeypatch.setattr(cell, "_configured_role", lambda *a: None, raising=False)
    monkeypatch.setattr(cell, "_load_selection_transaction", lambda *a: None, raising=False)
    monkeypatch.setattr(cell, "_ensure_snapshot", lambda *a: root / "snapshot", raising=False)
    monkeypatch.setattr(cell, "_profile_runtime_version", lambda *a: "1.0", raising=False)
    monkeypatch.setattr(
        cell, "_load_installation_context", lambda *a: context_owner, raising=False,
    )

    def provision(*_args: Any) -> None:
        provisioned.append(True)

    def complete(*_args: Any) -> None:
        raise error

    monkeypatch.setattr(cell, "_run_context", provision, raising=False)
    monkeypatch.setattr(cell, "_complete_slot", complete, raising=False)
    monkeypatch.setattr(cell, "_lexists", lambda *a: False, raising=False)
    monkeypatch.setattr(Path, "exists", lambda path: bool(provisioned) and path == slot)
    read_bytes = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: b"{}" if path.name == ".runtime-slot-ownership.json"
        else read_bytes(path),
    )
    validated = {
        "pluginRoot": str(root), "versionsRoot": str(slot.parent),
        "namespaceGeneration": "namespace", "generation": "install",
    }
    with ExitStack() as lifecycle, pytest.raises(type(error)) as caught:
        cell._provision_constructed(
            root, root, root / "context.json", "example",
            root, "1.0", validated, "token",
            SimpleNamespace(construction_lease=lambda *a: nullcontext()), lifecycle,
        )
    assert caught.value is error
    assert len(discarded) == (0 if uncertain else 1)
    assert not cell._lexists(slot / ".install-complete.json")
    if uncertain:
        assert error.process is process and error.job is job
        assert error.cleanup_resources == (("fixture", resource),)


@pytest.mark.parametrize("outcome", ["output-limit", "invalid-utf8", "uncertain"])
def test_invalid_target_rollback_distinguishes_unconfirmed_cleanup(
    cell: Any, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    helper = cell._cell_process_helpers()
    _, owner = helper._prepared_process_helpers()
    error = (
        owner.OutputLimitExceeded("stdout", 65536) if outcome == "output-limit" else
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid fixture byte")
        if outcome == "invalid-utf8" else
        owner.OwnedCleanupError(object(), object(), cleanup_resources=(("fixture", object()),))
    )
    root = Path("fixture-runtime")
    transaction = {
        "namespaceGeneration": "namespace", "installGeneration": "install",
        "management": {"path": str(root), "version": "1.0"},
        "target": {"manifest": {}}, "prior": {"manifest": {}},
    }
    rollbacks = []

    def capture(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    def validate(*_args: Any) -> None:
        helper.runtime_probe(
            root / "python", root, "pass", environment={}, error_type=cell.CellError,
            isolated_environment=helper.isolated_environment,
        )

    def rollback(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        rollbacks.append(kwargs["outcome"])
        return transaction

    monkeypatch.setattr(owner, "run_caller_owned", capture)
    managed, _ = helper._prepared_process_helpers()
    monkeypatch.setattr(managed, "resolve_runtime_python", lambda *a: str(root / "python"))
    monkeypatch.setattr(cell, "_plugin_version", lambda *a: "1.0", raising=False)
    monkeypatch.setattr(cell, "_normalized_path", str, raising=False)
    monkeypatch.setattr(cell, "_manifest_with_current_source", lambda m, *a: m, raising=False)
    monkeypatch.setattr(cell, "_validate_transaction_target", validate, raising=False)
    monkeypatch.setattr(cell, "_rollback_selection_transaction", rollback, raising=False)
    validated = {"pluginRoot": str(root), "namespaceGeneration": "namespace", "generation": "install"}
    with pytest.raises(owner.OwnedCleanupError if outcome == "uncertain" else cell.CellError) as caught:
        cell._resume_selection_transaction(
            root, root / "context.json", "example", root, validated, "token", transaction,
        )
    assert rollbacks == ([] if outcome == "uncertain" else ["restored-invalid-target"])
    if outcome == "uncertain":
        assert caught.value is error
    else:
        assert caught.value.__cause__ is error
