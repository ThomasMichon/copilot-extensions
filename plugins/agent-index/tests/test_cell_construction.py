"""Cell construction owns real leases until health/completion, not cutover."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import venv

import pytest

from test_installation_cells import CELL, PLUGIN


@pytest.mark.parametrize("explicit", [False, True])
def test_cell_process_environment_preserves_caller_and_filters_python_state(monkeypatch, explicit):
    incoming = {
        "PATH": "retained", "VIRTUAL_ENV": "retained",
        "PYTHONPATH": "outside", "PYTHONHOME": "outside",
        "PYTHONUTF8": "0", "PYTHONDONTWRITEBYTECODE": "0",
    }
    original = dict(incoming)
    if not explicit:
        monkeypatch.setattr(CELL.os, "environ", incoming)
    result = CELL._isolated_environment(incoming if explicit else None)
    assert incoming == original
    assert result == {
        "PATH": "retained", "VIRTUAL_ENV": "retained",
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }


@pytest.mark.parametrize("previous", [False, True])
def test_cell_construction_native_failure_retry_and_retention(tmp_path, monkeypatch, previous):
    tests = PLUGIN.parents[1] / "libs" / "installation-context" / "tests"
    monkeypatch.syspath_prepend(str(tests))
    spec = importlib.util.spec_from_file_location(
        "cell_construction_fixtures", tests / "test_snapshot_provenance.py",
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    layout = fixtures._receipt_layout(tmp_path, plugin_id="agent-index")
    fixtures._stamp_with_python(layout)
    context = fixtures._load_python_module()
    validated = context.validate_context_receipt(
        layout["install"], layout["durable"],
        expected_marketplace_id=layout["marketplace_id"],
        expected_plugin_id="agent-index", environment={},
    )
    runtime = CELL._load_versioned_runtime(PLUGIN)
    root = Path(validated["pluginRoot"])
    version = CELL._profile_runtime_version("1.0.0", None)
    if previous:
        old = runtime.slot(root, "0.9")
        (old / "unchanged").write_text("old")
        runtime.mark_complete(root, "0.9", payload_hash="old")
        runtime.activate(root, "0.9")
    prior = runtime.current_version(root)
    monkeypatch.setattr(CELL, "_load_versioned_runtime", lambda _payload: runtime)
    monkeypatch.setattr(CELL, "_load_selection_transaction", lambda *_args: None)
    monkeypatch.setattr(CELL, "_configured_role", lambda _env: None)
    monkeypatch.setattr(
        CELL, "_ensure_snapshot",
        lambda *_args: Path(layout["snapshots"]) / "1.0.0",
    )
    code = [23]
    attempts = []

    def build(_snapshot, slot, **_kwargs):
        attempts.append(slot)
        assert not (slot / "partial").exists()
        with runtime._cleanup_lease(root, version) as admitted:
            assert not admitted
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            quoted = str(slot / "partial").replace("'", "''")
            command = [
                shell, "-NoProfile", "-NonInteractive", "-Command",
                f"[IO.File]::WriteAllText('{quoted}', 'partial'); exit {code[0]}",
            ]
        else:
            command = [
                "bash", "-c", 'printf partial > "$1/partial"; exit "$2"',
                "dependency", str(slot), str(code[0]),
            ]
        CELL._run_checked(command, cwd=slot)
        venv.EnvBuilder(with_pip=False).create(slot)
        return CELL._venv_python(slot)

    monkeypatch.setattr(CELL, "_build_runtime", build)
    monkeypatch.setattr(CELL, "_runtime_import_probe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(CELL, "_prepare_selection_transaction", lambda *_args, **_kwargs: {})

    def settle(*_args):
        with runtime.construction_lease(root, version, timeout=0):
            pass
        with pytest.raises(runtime.RetentionTimeout):
            with runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
                pytest.fail("candidate retention was released before cell settlement")
        return {"status": "ready"}

    monkeypatch.setattr(CELL, "_resume_selection_transaction", settle)
    arguments = (
        PLUGIN, Path(layout["payload"]), Path(layout["install"]),
        layout["marketplace_id"], Path(layout["durable"]), "1.0.0",
        validated, "owned-cell-lock",
    )
    with pytest.raises(CELL.CellError, match="exit 23"):
        CELL._provision_locked(*arguments)
    slot = root / "versions" / version
    assert not slot.exists()
    assert runtime.current_version(root) == prior
    code[0] = 0
    assert CELL._provision_locked(*arguments) == {"status": "ready"}
    assert json.loads((slot / ".runtime-slot-completion.json").read_text())["runtime"]["version"] == version
    with runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
        pass
    assert len(attempts) == 2
    assert runtime.current_version(root) == prior
    if previous:
        assert (old / "unchanged").read_text() == "old"
