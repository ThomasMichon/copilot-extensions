"""Helpers use their queued application's interpreter, not a draining worker's hint."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest
import update_driver

from worktree_manager import update_driver_cli as manager


KEY = "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"


@pytest.mark.parametrize("prepared", [False, True])
def test_request_python_uses_the_application_interpreter_and_restores_ambient_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool,
) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    selected = str(Path(sys.executable).resolve())
    if prepared:
        monkeypatch.setenv(KEY, selected)
    else:
        monkeypatch.delenv(KEY, raising=False)
    context = manager.request_context(capture_engine=False)
    assert KEY not in context

    monkeypatch.setenv(KEY, "different-worker-selection")
    with pytest.raises(RuntimeError, match="controlled callback failure"):
        with manager._request_environment(context):
            assert os.environ[KEY] == sys.executable
            raise RuntimeError("controlled callback failure")
    assert os.environ[KEY] == "different-worker-selection"


def test_real_queued_children_do_not_inherit_the_draining_workers_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    queue = tmp_path / "queue"
    selected = str(Path(sys.executable).resolve())
    program = f"""
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from worktree_manager.update_driver_cli import _request_environment
report={{"before":os.environ.get({KEY!r}),"interpreter":sys.executable}}
with _request_environment({{}}):
    report["during"]=os.environ.get({KEY!r})
report["after"]=os.environ.get({KEY!r})
Path(sys.argv[1]).write_text(json.dumps(report),encoding="utf-8")
"""
    for prepared in (True, False):
        if prepared:
            monkeypatch.setenv(KEY, selected)
        else:
            monkeypatch.delenv(KEY, raising=False)
        context = manager.request_context(capture_engine=False)
        report = tmp_path / f"child-{prepared}.json"
        request = update_driver.enqueue(
            update_driver.RequestSpec(
                backend="manager", argv=(str(prepared),), cwd=str(tmp_path),
                source="prepared-python-context", context=tuple(context.items()),
            ),
            runner=update_driver.TrustedRunner(
                (sys.executable, "-I", "-S", "-B", "-c", program, str(report),
                 str(Path(__file__).resolve().parents[1] / "src")),
                str(tmp_path), context_keys=manager._RUNNER_CONTEXT_KEYS,
            ),
            root=queue,
        )
        monkeypatch.setenv(KEY, "different-worker-selection")
        assert update_driver.drain(root=queue) == 0
        completed = update_driver.status(request.id, root=queue)
        assert not isinstance(completed, tuple)
        assert completed.state == "completed" and completed.exit_code == 0
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["before"] is None and result["after"] is None
        assert result["during"] == result["interpreter"]
        assert Path(result["during"]).resolve() == Path(selected)
        assert os.environ[KEY] == "different-worker-selection"
