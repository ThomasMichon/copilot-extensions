"""Native producer orchestration against fixed controlled inner helpers."""

import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

import agent_procutil as pu
from update_driver import InterruptedError, RequestSpec, TrustedRunner, enqueue, foundation_status
from update_driver.foundation_inputs import GENERATED, PACKAGES, inspect

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual native Windows producer")


@pytest.fixture
def producer_source(area):
    library = Path(__file__).resolve().parents[2]
    copied = area / "inputs"

    def ignored(_directory, names):
        return {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        }

    for name in PACKAGES:
        shutil.copytree(library / name, copied / name, ignore=ignored)
    source = copied / "update-driver"
    fixtures = Path(__file__).parent
    shutil.copyfile(
        fixtures / "fixture_foundation_prerequisites.ps1",
        source / "scripts" / "installer-engine.ps1",
    )
    shutil.copyfile(
        fixtures / "fixture_foundation_builder.py", source / "scripts" / "foundation-build.py",
    )
    return source


def command(source, root, digest=None):
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return [
        str(shell), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-File", str(source / "scripts" / "foundation-produce.ps1"),
        "-Root", str(root), "-SourceHash", digest or inspect(source).digest,
    ]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_native_producer_admits_before_prerequisites_and_completes_exact_status(
    producer_source, area, exit_code,
):
    root = area / "state"
    digest = inspect(producer_source).digest
    env = dict(
        os.environ, FOUNDATION_FIXTURE_PYTHON=sys.executable,
        FOUNDATION_FIXTURE_EXIT=str(exit_code),
        UPDATE_DRIVER_WITNESS='{"fixture":"foreign ambient transport is not custody"}',
    )
    result = subprocess.run(
        command(producer_source, root, digest), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120, **pu.no_window_kwargs(),
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    operation = foundation_status(root=root)
    assert operation.source_hash == digest and operation.exit_code == exit_code
    assert operation.state == ("completed" if exit_code == 0 else "failed")
    assert operation.error == (None if exit_code == 0 else "builder_failed")
    assert (root / "prerequisite-started").read_text() == operation.operation_id
    assert (root / "builder-finished").exists()
    assert not (root / "queue.sqlite3").exists()
    # The bootstrap venv/base is retained, not removed while another environment can depend on it.
    assert (root / "bootstrap" / operation.operation_id / "venv" / "pyvenv.cfg").exists()
    frozen = root / "bootstrap" / operation.operation_id / "inputs" / "update-driver"
    assert inspect(frozen).digest == digest
    assert inspect(producer_source).digest == digest


def test_native_producer_source_mismatch_never_acquires_prerequisites(producer_source, area):
    root = area / "state"
    result = subprocess.run(
        command(producer_source, root, "f" * 64), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **pu.no_window_kwargs(),
    )
    assert result.returncode == 125
    assert foundation_status(root=root) is None
    assert not (root / "bootstrap").exists()
    assert not (root / "prerequisite-started").exists()


def test_native_producer_defers_working_linked_uv_to_canonical_physical_selection(producer_source, area):
    helper = Path(__file__).parents[1] / "scripts" / "installer_dependency_config.py"
    spec = importlib.util.spec_from_file_location("_foundation_linked_uv", helper)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    physical_uv = config.select_uv_executable("uv", error_type=RuntimeError)
    # Hosted UV is a regular path; local WinGet discovery may already include an NT namespace.
    if physical_uv.startswith("\\\\?\\UNC\\"):
        physical_uv = "\\\\" + physical_uv[8:]
    elif physical_uv.startswith("\\\\?\\"):
        physical_uv = physical_uv[4:]
    link_dir = area / "linked-tools"
    link_dir.mkdir()
    logical_uv = link_dir / "uv.exe"
    try:
        logical_uv.symlink_to(physical_uv)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("native file symlink privilege unavailable")
        raise
    library = Path(__file__).resolve().parents[2]
    canonical = (library / "installer-engine" / "installer-engine.ps1").read_text()
    fixture = Path(__file__).with_name("fixture_foundation_prerequisites.ps1").read_text()
    # Exercise real Ensure-Uv discovery/probing; only interpreter acquisition is controlled.
    venv_control = "function New-SignedVenv" + fixture.split("function New-SignedVenv", 1)[1]
    (producer_source / "scripts" / "installer-engine.ps1").write_text(canonical + "\n" + venv_control)
    root = area / "state"
    env = dict(
        os.environ, PATH=str(link_dir) + os.pathsep + os.environ.get("PATH", ""),
        FOUNDATION_FIXTURE_PYTHON=sys.executable, FOUNDATION_FIXTURE_CHECK_UV="1",
        FOUNDATION_FIXTURE_UV_TARGET=physical_uv,
    )
    result = subprocess.run(
        command(producer_source, root), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120, **pu.no_window_kwargs(),
    )
    logs = "\n".join(
        f"{path.name}:\n{path.read_text(encoding='utf-8', errors='replace')[-12000:]}"
        for path in root.glob("*.log")
    )
    assert result.returncode == 0, result.stdout + result.stderr + logs
    completed = foundation_status(root=root)
    assert completed.state == "completed" and completed.exit_code == 0
    prerequisites = json.loads((
        root / "bootstrap" / completed.operation_id / "prerequisites.json"
    ).read_text())
    assert Path(prerequisites["uv"]) == logical_uv
    assert os.path.samefile((root / "uv-selected").read_text(), physical_uv)


def test_native_producer_never_creates_control_state_inside_its_source(producer_source):
    root = producer_source / "state"
    before = inspect(producer_source).digest
    result = subprocess.run(
        command(producer_source, root, before), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **pu.no_window_kwargs(),
    )
    assert result.returncode != 0 and "source closure" in " ".join(result.stderr.split()), result.stderr
    assert not root.exists()
    assert inspect(producer_source).digest == before


def test_native_submit_facade_preserves_contained_caller_job_suppression(producer_source, area):
    from test_foundation_genesis_process import pinned_worker

    root = area / "state"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    fixture = Path(__file__).with_name("fixture_foundation_submit.ps1")
    env = dict(
        os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1", FOUNDATION_FIXTURE_PYTHON=sys.executable,
        FOUNDATION_FIXTURE_HOLD="1",
    )
    with (area / "submit.log").open("w") as log:
        caller, job = pu.spawn_sync_in_kill_on_close_job(
            [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
             str(root), str(producer_source), "contained"],
            env=env, require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **pu.no_window_kwargs(),
        )
        worker = None
        try:
            assert caller.wait(timeout=90) == 0, (area / "submit.log").read_text()
            worker = pinned_worker(json.loads((root / "fixture-contained.json").read_text()))
            deadline = time.monotonic() + 90
            while not (root / "builder-started").exists():
                assert worker.poll() is None, [
                    path.read_text(errors="replace") for path in root.glob("*.log")
                ]
                assert time.monotonic() < deadline
                time.sleep(.02)
            job.close()
            job = None
            worker.wait(timeout=10)
            assert not (root / "builder-finished").exists()
            assert foundation_status(root=root).state == "running"
        finally:
            if worker is not None:
                try:
                    if worker.poll() is None:
                        worker.terminate()
                        worker.wait(timeout=10)
                finally:
                    worker.close()
            if job is not None:
                job.close()
            if caller.poll() is None:
                caller.kill()
            caller.wait(timeout=10)


def test_native_producer_owner_death_does_not_orphan_the_owned_builder(producer_source, area):
    root = area / "state"
    root.mkdir()
    env = dict(
        os.environ, FOUNDATION_FIXTURE_PYTHON=sys.executable, FOUNDATION_FIXTURE_HOLD="1",
    )
    with (root / "fixture.log").open("w") as log:
        owner, job = pu.spawn_sync_in_kill_on_close_job(
            command(producer_source, root), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **pu.no_window_kwargs(),
        )
        try:
            deadline = time.monotonic() + 90
            while not (root / "builder-started").exists():
                assert owner.poll() is None, (root / "fixture.log").read_text()
                assert time.monotonic() < deadline, (root / "fixture.log").read_text()
                time.sleep(.02)
            admitted = foundation_status(root=root)
            queued = enqueue(
                RequestSpec("producer-loss-fixture"), root=root,
                runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
            )
            assert queued.state == "pending"
            owner.kill()
            owner.wait(timeout=5)
            (root / "release-builder").touch()
            successor = subprocess.run(
                command(producer_source, root), env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60, **pu.no_window_kwargs(),
            )
            assert successor.returncode == 125
            native_interrupted = foundation_status(root=root)
            assert native_interrupted.state == "interrupted"
            assert native_interrupted.error == "owner_lost_custody"
            assert native_interrupted.operation_id == admitted.operation_id
            with pytest.raises(InterruptedError, match="foundation"):
                enqueue(
                    RequestSpec("producer-loss-later"), root=root,
                    runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
                )
            interrupted = foundation_status(root=root)
            assert interrupted.operation_id == admitted.operation_id
            assert interrupted.state == "interrupted" and interrupted.exit_code is None
            time.sleep(.2)
            assert not (root / "builder-finished").exists()
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=5)
            if job is not None:
                job.close()
