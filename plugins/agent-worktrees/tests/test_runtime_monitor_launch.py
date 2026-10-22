from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees.runtime_monitor_launch import retained_monitor_argv
from agent_worktrees.status_monitor_python import direct_monitor_python


@pytest.fixture
def runtime():
    import importlib.util

    source = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("launch_fixture_runtime", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("complete,owner_dies", [(False, False), (True, False), (True, True)])
def test_retained_bootstrap_pins_before_pth_or_application_imports(
    runtime, tmp_path, complete, owner_dies,
):
    from zdd import diagnostics

    root = tmp_path / "runtime"
    slot = root / "versions" / "1.0.0"
    base_python = os.environ.get("COPILOT_EXTENSIONS_TEST_BASE_PYTHON", sys.executable)
    created = subprocess.run(
        [base_python, "-I", "-B", "-m", "venv", "--without-pip", "--copies", str(slot)],
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert created.returncode == 0, created.stderr
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for source in [scripts / "runtime_launcher.py", *scripts.glob("versioned_runtime*.py")]:
        shutil.copyfile(source, bootstrap / source.name)
    python = runtime.slot_python(root, slot.name)
    site_packages = Path(subprocess.check_output(
        [str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        text=True, timeout=30, **no_window_kwargs(),
    ).strip())
    site_packages.mkdir(parents=True, exist_ok=True)
    ready = tmp_path / "ready.json"
    stop = tmp_path / "stop"
    (site_packages / "retention_probe.py").write_text(
        "import json, pathlib, sys\n"
        "runtime = sys.modules['_retained_versioned_runtime']\n"
        f"root = pathlib.Path({str(root)!r})\n"
        "try:\n"
        "    with runtime._kernel_lease(root, '1.0.0', kind='retention', "
        "shared=False, timeout=0):\n"
        "        raise AssertionError('site imported without process retention')\n"
        "except runtime.RetentionTimeout:\n"
        f"    pathlib.Path({str(ready)!r}).write_text(json.dumps({{'prefix':sys.prefix}}))\n",
        encoding="utf-8",
    )
    (site_packages / "retention.pth").write_text("import retention_probe\n", encoding="utf-8")
    (site_packages / "retention_application.py").write_text(
        "import pathlib,time\n"
        f"stop=pathlib.Path({str(stop)!r})\n"
        "while not stop.exists(): time.sleep(0.05)\n",
        encoding="utf-8",
    )
    if complete:
        runtime.mark_complete(root, slot.name, payload_hash="frozen")
    argv = retained_monitor_argv([str(python), "-m", "retention_application"])
    argv[0], extra = direct_monitor_python(str(python))
    pid_file = tmp_path / "child-pid"
    child_log = tmp_path / "child.log"
    if owner_dies:
        parent_code = (
            "import os,pathlib,subprocess,time\n"
            f"log=open({str(child_log)!r},'w')\n"
            f"child=subprocess.Popen({argv!r},stdout=log,stderr=log,"
            f"env={{**os.environ,**{extra!r}}},"
            f"creationflags={subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0})\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        launch = [sys.executable, "-I", "-B", "-c", parent_code]
    else:
        launch = argv
    process = subprocess.Popen(
        launch, env=dict(os.environ) if owner_dies else {**os.environ, **extra},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    child_identity = None
    child_pid = None
    try:
        if not complete:
            output, error = process.communicate(timeout=30)
            assert process.returncode != 0
            assert "attributable completion" in error, (output, error)
            assert not ready.exists(), "site/application imported before retention admission"
            return
        deadline = time.monotonic() + 30
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), (
            process.communicate(timeout=5) if process.poll() is not None else "bootstrap stalled"
        )
        assert Path(json.loads(ready.read_text())["prefix"]).resolve() == slot.resolve()
        if owner_dies:
            child_pid = int(pid_file.read_text())
            child_identity = diagnostics.process_start_time(child_pid)
            assert child_identity is not None
            process.terminate()
            process.communicate(timeout=10)
            assert diagnostics._pid_alive(child_pid), "runtime died with its launching owner"
        with pytest.raises(runtime.RetentionTimeout):
            with runtime._kernel_lease(root, slot.name, kind="retention", shared=False, timeout=0):
                pytest.fail("running application lost its process-owned retention")
        assert runtime.gc(root) == []
        stop.touch()
        if owner_dies:
            deadline = time.monotonic() + 30
            while diagnostics._pid_alive(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not diagnostics._pid_alive(child_pid), child_log.read_text()
        else:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, (output, error)
        assert runtime.gc(root) == [slot.name]
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)
        if child_pid is not None and child_identity is not None:
            diagnostics.terminate_pid_if_identity(child_pid, child_identity, exit_timeout=10)


def test_original_slot_is_preserved_before_interpreter_conversion(tmp_path):
    slot = tmp_path / "versions" / "1.0.0"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    for name in ("runtime_launcher.py", "versioned_runtime.py"):
        (bootstrap / name).write_text("fixture", encoding="utf-8")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    original = [str(python), "-m", "agent_worktrees", "status-monitor"]
    assert retained_monitor_argv(original) == [
        str(python), "-I", "-S", "-B", str(bootstrap / "runtime_launcher.py"), *original[1:],
    ]
    (bootstrap / "versioned_runtime.py").unlink()
    with pytest.raises(RuntimeError, match="bootstrap is invalid"):
        retained_monitor_argv(original)
