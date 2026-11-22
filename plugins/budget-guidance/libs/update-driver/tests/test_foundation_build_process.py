"""Opt-in real immutable construction, never a host foundation installation."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import agent_procutil
from agent_procutil.owned_process import run_caller_owned
from update_driver import RequestSpec, TrustedRunner, drain, enqueue, foundation_state as fs
from update_driver.foundation_inputs import GENERATED, PACKAGES, inspect

pytestmark = pytest.mark.skipif(
    os.environ.get("UPDATE_DRIVER_FOUNDATION_BUILD") != "1",
    reason="explicit canonical foundation construction qualification",
)


def test_real_foundation_reuses_completion_and_survives_source_removal(area, monkeypatch, rename_source):
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("foundation construction qualification requires canonical uv")
    source = Path(__file__).resolve().parents[1]
    inputs = area / "inputs"

    def ignored(_directory, names):
        return {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        }

    for name in PACKAGES:
        shutil.copytree(source.parent / name, inputs / name, ignore=ignored)
    plugin = inputs / "update-driver"
    selected = inspect(plugin)
    root = area / "state"
    monkeypatch.setenv("UV_CACHE_DIR", str(area / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_NO_PROGRESS", "1")
    marker = None
    version = "foundation-" + selected.digest
    for iteration in range(3):
        if iteration == 2:
            health_source = plugin / "src" / "update_driver" / "foundation_inputs.py"
            with health_source.open("a", encoding="utf-8") as stream:
                stream.write('\ndef check_health():\n    raise RuntimeError("fixture candidate health failure")\n')
        admitted_hash = inspect(plugin).digest
        enqueue(
            RequestSpec("foundation-process-fixture", argv=(str(iteration),)), root=root,
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        )

        def executor(transaction):
            operation = fs.begin(root, admitted_hash, transaction._lock)
            witness = transaction.participant_env()["UPDATE_DRIVER_WITNESS"]
            old = os.environ.get("UPDATE_DRIVER_WITNESS")
            os.environ["UPDATE_DRIVER_WITNESS"] = witness
            try:
                result = run_caller_owned(
                    [sys.executable, "-I", "-B", "-X", "utf8",
                     str(plugin / "scripts" / "foundation-build.py"),
                     "--root", str(root), "--source-hash", admitted_hash,
                     "--uv", uv, "--base-python", sys.executable],
                    timeout=240, max_stdout_bytes=65536, max_stderr_bytes=65536,
                )
            finally:
                if old is None:
                    os.environ.pop("UPDATE_DRIVER_WITNESS", None)
                else:
                    os.environ["UPDATE_DRIVER_WITNESS"] = old
            if iteration < 2 and result.returncode != 0:
                pytest.fail(f"canonical foundation construction failed:\n{result.stdout}\n{result.stderr}")
            if iteration == 2:
                assert result.returncode != 0 and "fixture candidate health failure" in result.stderr
            fs.finish(root, operation, transaction._lock, exit_code=result.returncode,
                      error=None if result.returncode == 0 else "builder_failed")
            return result.returncode

        drain(root=root, executor=executor)
        target = root / "foundation"
        assert (target / "current-version").read_text().strip() == version
        slot = target / "versions" / version
        current_marker = (slot / ".install-complete.json").read_bytes()
        if marker is not None:
            assert current_marker == marker
        marker = current_marker
        assert json.loads(marker)["payload_hash"]
        if iteration == 2:
            assert not (
                target / "versions" / ("foundation-" + admitted_hash) / ".install-complete.json"
            ).exists()
            assert fs.read(root).state == "failed"
    rename_source(inputs, area / "removed-source")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    health = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "from update_driver.foundation_inputs import check_health; check_health()"],
        capture_output=True, text=True, timeout=20, **agent_procutil.no_window_kwargs(),
    )
    assert health.returncode == 0, (health.stdout, health.stderr)
    worker = subprocess.run(
        [str(python), "-I", "-S", "-B", "-X", "utf8",
         str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
         "-m", "update_driver", "worker", "--root", str(root)],
        capture_output=True, text=True, timeout=20, **agent_procutil.no_window_kwargs(),
    )
    assert worker.returncode == 0, (worker.stdout, worker.stderr)
