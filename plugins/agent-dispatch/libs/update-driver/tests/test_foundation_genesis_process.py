"""Opt-in real downloads/construction through the Python-free native front door."""

import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

import agent_procutil as pu
from agent_procutil.daemon_broker import WindowsDaemonProcess
from update_driver import RequestSpec, TrustedRunner, enqueue, foundation_status, wait
from update_driver.foundation_inputs import GENERATED, PACKAGES, inspect

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS") != "1",
    reason="explicit isolated Windows native genesis, network and caller-Job qualification",
)


def observe(predicate, timeout, diagnostic):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError(diagnostic())


def pinned_worker(receipt):
    process = WindowsDaemonProcess(receipt["pid"], ["isolated-native-foundation"])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
    try:
        assert kernel.GetProcessTimes(
            process._handle, ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(system), ctypes.byref(user),
        )
        assert (created.dwLowDateTime | created.dwHighDateTime << 32) == receipt["created"]
        return process
    except BaseException:
        process.close()
        raise


def window_observer(library):
    source = library / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py"
    spec = importlib.util.spec_from_file_location("_foundation_genesis_observer", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    user, kernel, callback = module._configure_win32()
    return module._WindowObserver(user, kernel, callback)


def test_real_cold_callers_converge_after_submitter_job_loss_and_source_removal(area):
    library = Path(__file__).resolve().parents[2]
    inputs = area / "inputs"

    def ignored(_directory, names):
        return {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        }

    for name in PACKAGES:
        shutil.copytree(library / name, inputs / name, ignore=ignored)
    plugin = inputs / "update-driver"
    digest = inspect(plugin).digest
    root = area / "cold"
    assert not root.exists()
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    fixture = Path(__file__).with_name("fixture_foundation_submit.ps1")
    env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"))
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    # Do not borrow registry-installed Python or a shared interpreter/cache from the orchestrator.
    env.update(UV_PYTHON_PREFERENCE="only-managed", UV_PYTHON_DOWNLOADS="automatic")
    workers, parents, jobs = [], [], []

    def diagnostics():
        values = [
            f"{path.name}:\n{path.read_text(encoding='utf-8', errors='replace')[-12000:]}"
            for path in root.glob("*.log")
        ] if root.exists() else ["native root absent"]
        return "\n".join(values)

    def submit(label):
        log = (area / f"submit-{label}.log").open("w")
        try:
            parent, job = pu.spawn_sync_in_kill_on_close_job(
                [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
                 str(root), str(plugin), label], env=env, require_job=True, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            parents.append(parent)
            jobs.append(job)
            assert parent.wait(timeout=120) == 0, (area / f"submit-{label}.log").read_text()
            receipt = json.loads((root / f"fixture-{label}.json").read_text())
            worker = pinned_worker(receipt)
            workers.append(worker)
            return worker, job
        finally:
            log.close()

    with window_observer(library) as observation:
        try:
            first, first_job = submit("first")
            observe(
                lambda: foundation_status(root=root) is not None or first.poll() is not None,
                90, diagnostics,
            )
            admitted = foundation_status(root=root)
            assert admitted is not None and admitted.state == "running", diagnostics()
            assert first.poll() is None, diagnostics()
            first_job.close()
            jobs[0] = None
            assert first.poll() is None, "the admitted producer died with its submitter Job"
            queued = enqueue(
                RequestSpec("foundation-genesis-fixture"), root=root,
                runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(7)"), str(area)),
            )
            assert queued.state == "pending"
            second, _ = submit("second")
            assert first.wait(timeout=600) == 0, diagnostics()
            assert second.wait(timeout=120) == 0, diagnostics()
            completed = foundation_status(root=root)
            assert completed.operation_id == admitted.operation_id
            assert completed.source_hash == digest and completed.state == "completed"
            assert completed.exit_code == 0
            assert [path.name for path in (root / "bootstrap").iterdir()] == [admitted.operation_id]
            prerequisites = json.loads((
                root / "bootstrap" / admitted.operation_id / "prerequisites.json"
            ).read_text())
            assert Path(prerequisites["uv"]).is_relative_to(root / "bootstrap" / admitted.operation_id)
            base = root / "bootstrap" / admitted.operation_id / "python"
            assert base.is_dir() and list(base.iterdir()), "managed CPython was not acquired privately"
            target = root / "foundation"
            version = "foundation-" + digest
            assert (target / "current-version").read_text().strip() == version
            slot = target / "versions" / version
            assert (slot / ".install-complete.json").is_file()
            inputs.rename(area / "removed-inputs")
            (root / "bootstrap" / admitted.operation_id / "inputs").rename(area / "removed-frozen-inputs")
            python = slot / "Scripts" / "python.exe"
            launcher = slot / ".runtime-bootstrap" / "runtime_launcher.py"
            for module, args in [
                ("update_driver.foundation_inputs", []),
                ("update_driver", ["worker", "--root", str(root)]),
            ]:
                result = subprocess.run(
                    [str(python), "-I", "-S", "-B", "-X", "utf8", str(launcher), "-m", module, *args],
                    capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
                )
                assert result.returncode == 0, result.stdout + result.stderr
            result = wait(queued.id, root=root, timeout=5)
            assert result.state == "completed" and result.exit_code == 7
            qualify_real_upgrade(
                area=area, library=library, root=root, slot=slot, env=env,
                parents=parents, diagnostics=diagnostics,
            )
        finally:
            for worker in workers:
                try:
                    if worker.poll() is None:
                        worker.terminate()
                        worker.wait(timeout=15)
                finally:
                    worker.close()
            for job in jobs:
                if job is not None:
                    job.close()
            for parent in parents:
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=10)
        assert observation.surfaced == {}
        assert observation.focus_changes == {}


def qualify_real_upgrade(*, area, library, root, slot, env, parents, diagnostics):
    """Upgrade real installed packages while the old immutable worker remains usable."""
    from test_foundation_producer_process import command
    from test_foundation_upgrade_process import worker_command

    inputs = area / "upgrade-inputs"
    for name in PACKAGES:
        shutil.copytree(
            library / name, inputs / name,
            ignore=shutil.ignore_patterns(*GENERATED, "*.egg-info", "*.dist-info", "*.pyc", "*.pyo"),
        )
    plugin = inputs / "update-driver"
    (plugin / "upgrade.txt").write_text("same package version, distinct qualified inputs")
    previous = foundation_status(root=root)
    digest = inspect(plugin).digest
    assert digest != previous.source_hash
    marker = (slot / ".install-complete.json").read_bytes()
    script = (
        "from pathlib import Path; import time\n"
        f"root=Path({str(root)!r})\n"
        "(root/'upgrade-active').touch()\n"
        "deadline=time.monotonic()+90\n"
        "while not (root/'upgrade-release').exists():\n"
        " assert time.monotonic()<deadline\n"
        " time.sleep(.02)\n"
        "raise SystemExit(11)\n"
    )
    active = enqueue(
        RequestSpec("installed-upgrade-active"), root=root,
        runner=TrustedRunner((sys.executable, "-c", script), str(area)),
    )
    with (root / "upgrade-processes.log").open("w") as log:
        old_worker = subprocess.Popen(
            worker_command(slot, root), stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **pu.no_window_kwargs(),
        )
        parents.append(old_worker)
        observe(lambda: (root / "upgrade-active").exists(), 30, diagnostics)
        producer = subprocess.Popen(
            command(plugin, root, digest), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **pu.no_window_kwargs(),
        )
        parents.append(producer)
        time.sleep(2)
        assert producer.poll() is None and foundation_status(root=root) == previous
        (root / "upgrade-release").touch()
        assert old_worker.wait(timeout=30) == 0, diagnostics()
        assert wait(active.id, root=root, timeout=5).exit_code == 11
        observe(
            lambda: foundation_status(root=root).operation_id != previous.operation_id
            or producer.poll() is not None,
            60, diagnostics,
        )
        admitted = foundation_status(root=root)
        assert admitted.state == "running" and admitted.source_hash == digest, diagnostics()
        pending = enqueue(
            RequestSpec("installed-upgrade-pending"), root=root,
            runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(13)"), str(area)),
        )
        retained_worker = subprocess.Popen(
            worker_command(slot, root), stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **pu.no_window_kwargs(),
        )
        parents.append(retained_worker)
        assert producer.wait(timeout=600) == 0, diagnostics()
        assert retained_worker.wait(timeout=60) == 0, diagnostics()
    completed = foundation_status(root=root)
    assert completed.operation_id == admitted.operation_id and completed.state == "completed"
    assert (root / "foundation" / "current-version").read_text().strip() == "foundation-" + digest
    assert (slot / ".install-complete.json").read_bytes() == marker
    result = wait(pending.id, root=root, timeout=5)
    assert result.state == "completed" and result.exit_code == 13
    # Remove both source closures again; only installed slots and required interpreter files remain.
    inputs.rename(area / "removed-upgrade-inputs")
    (root / "bootstrap" / admitted.operation_id / "inputs").rename(area / "removed-upgrade-frozen")
    for current in (slot, root / "foundation" / "versions" / ("foundation-" + digest)):
        python = current / "Scripts" / "python.exe"
        launcher = current / ".runtime-bootstrap" / "runtime_launcher.py"
        result = subprocess.run(
            [str(python), "-I", "-S", "-B", "-X", "utf8", str(launcher),
             "-m", "update_driver.foundation_inputs"],
            capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
    later = enqueue(
        RequestSpec("installed-upgrade-later"), root=root,
        runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(17)"), str(area)),
    )
    result = subprocess.run(
        worker_command(root / "foundation" / "versions" / ("foundation-" + digest), root),
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = wait(later.id, root=root, timeout=5)
    assert result.state == "completed" and result.exit_code == 17
