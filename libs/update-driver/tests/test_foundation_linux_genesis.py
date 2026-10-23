"""Explicit real Linux cold acquisition, native custody, construction and retained upgrade."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd
from update_driver import RequestSpec, TrustedRunner, enqueue, status
from update_driver import foundation_state as fs
from update_driver.foundation_inputs import GENERATED, PACKAGES, inspect

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("UPDATE_DRIVER_NATIVE_LINUX_GENESIS") != "1",
    reason="explicit isolated Linux native genesis and network qualification",
)


def observe(predicate, timeout, diagnostic):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError(diagnostic())


def test_actual_private_genesis_and_queue_preserving_upgrade_after_caller_group_loss(area):
    library = Path(__file__).resolve().parents[2]
    inputs = area / "inputs"

    def ignored(_directory, names):
        return {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        }

    for name in PACKAGES:
        shutil.copytree(library / name, inputs / name, ignore=ignored)
    source = inputs / "update-driver"
    root = area / "cold"
    tools = area / "native-tools"
    tools.mkdir()
    for name in (
        "bash", "dirname", "uname", "readlink", "stat", "find", "sort", "sha256sum", "iconv",
        "cat", "mkdir", "sync", "awk", "date", "mktemp", "mv", "rm", "cp", "flock", "grep",
        "sleep", "setsid", "mkfifo", "rmdir", "curl", "tar", "gzip", "head", "chmod", "sed", "tr", "ps",
    ):
        executable = shutil.which(name)
        assert executable, f"native tool missing: {name}"
        (tools / name).symlink_to(executable)
    env = dict(os.environ, PATH=str(tools), UV_PYTHON_DOWNLOADS="automatic")
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env.pop("UPDATE_DRIVER_WITNESS", None)
    descriptors = []
    parents = []
    old_worker = None
    worker_log = None
    release = area / "release-old"

    def diagnostics():
        return "\n".join(
            f"{path.relative_to(root)}:\n{path.read_text(errors='replace')[-12000:]}"
            for path in root.rglob("*.log")
        )

    def submit(plugin):
        digest = inspect(plugin).digest
        # Parent's process group is the cleanup boundary destroyed after admission.
        script = (
            '"$1" "$2" "$3" "$4" >"$5"; '
            'printf "ready\\n" >"$6"; sleep 300'
        )
        label = str(len(parents))
        receipt, ready = area / f"launch-{label}", area / f"ready-{label}"
        parent = subprocess.Popen(
            [str(tools / "bash"), "-c", script, "foundation-caller", str(tools / "bash"),
             str(plugin / "scripts" / "foundation-submit.sh"), str(root), digest,
             str(receipt), str(ready)],
            env=env, start_new_session=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        parents.append(parent)
        observe(lambda: ready.exists() or parent.poll() is not None, 30, diagnostics)
        assert ready.exists(), diagnostics()
        frame = receipt.read_text().strip()
        assert frame.startswith("launched:")
        descriptor = open_pidfd(int(frame.partition(":")[2]))
        descriptors.append(descriptor)
        assert not _pidfd_exited(descriptor), diagnostics()
        os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        assert not _pidfd_exited(descriptor), "native producer died with caller containment"
        return descriptor

    def selected_slot(digest):
        version = "foundation-" + digest
        assert (root / "foundation" / "current-version").read_text().strip() == version
        return root / "foundation" / "versions" / version

    def run_slot(slot, module, *args):
        result = subprocess.run(
            [str(slot / "bin" / "python"), "-I", "-S", "-B", "-X", "utf8",
             str(slot / ".runtime-bootstrap" / "runtime_launcher.py"), "-m", module, *args],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr + diagnostics()

    try:
        digest = inspect(source).digest
        first = submit(source)
        observe(lambda: fs.read(root) is not None or _pidfd_exited(first), 90, diagnostics)
        admitted = fs.read(root)
        assert admitted is not None and admitted.state == "running", diagnostics()
        second = submit(source)
        observe(lambda: _pidfd_exited(first) and _pidfd_exited(second), 600, diagnostics)
        completed = fs.read(root)
        assert completed.state == "completed" and completed.exit_code == 0, diagnostics()
        assert completed.operation_id == admitted.operation_id
        assert completed.source_hash == digest
        assert len(list((root / "bootstrap").iterdir())) == 1
        bootstrap = root / "bootstrap" / admitted.operation_id
        assert list((bootstrap / "python").iterdir()), "no private managed CPython acquisition"
        assert str(bootstrap).encode() in (bootstrap / "uv-path").read_bytes()
        slot = selected_slot(digest)
        run_slot(slot, "update_driver.foundation_inputs")
        started = area / "old-started"
        active = enqueue(
            RequestSpec("linux-foundation-old-active"), root=root,
            runner=TrustedRunner((
                str(slot / "bin" / "python"), "-I", "-c",
                "from pathlib import Path\nimport sys,time\n"
                "Path(sys.argv[1]).write_text('started')\n"
                "while not Path(sys.argv[2]).exists(): time.sleep(.02)\n"
                "raise SystemExit(7)", str(started), str(release),
            ), str(area)),
        )
        worker_log = (root / "old-worker.log").open("w")
        old_worker = subprocess.Popen(
            [str(slot / "bin" / "python"), "-I", "-S", "-B", "-X", "utf8",
             str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
             "-m", "update_driver", "worker", "--root", str(root)],
            env=env, start_new_session=True, stdin=subprocess.DEVNULL,
            stdout=worker_log, stderr=worker_log,
        )
        observe(lambda: started.exists() or old_worker.poll() is not None, 30, diagnostics)
        assert started.exists() and status(active.id, root=root).state == "running", diagnostics()
        next_inputs = area / "next-inputs"
        shutil.copytree(inputs, next_inputs, ignore=ignored)
        next_source = next_inputs / "update-driver"
        (next_source / "revision.txt").write_text("new immutable source\n")
        upgraded_digest = inspect(next_source).digest
        upgrade = submit(next_source)
        assert fs.read(root) == completed, "upgrade bypassed the active worker's installation lock"
        assert selected_slot(digest) == slot and old_worker.poll() is None
        release.write_text("release\n")
        assert old_worker.wait(timeout=30) == 0, diagnostics()
        assert status(active.id, root=root).exit_code == 7
        observe(
            lambda: fs.read(root).state == "running" or _pidfd_exited(upgrade), 30, diagnostics,
        )
        assert fs.read(root).state == "running", diagnostics()
        pending = enqueue(
            RequestSpec("linux-foundation-upgrade-fixture"), root=root,
            runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(17)"), str(area)),
        )
        observe(lambda: _pidfd_exited(upgrade), 600, diagnostics)
        assert fs.read(root).state == "completed", diagnostics()
        newer = selected_slot(upgraded_digest)
        assert newer != slot and slot.is_dir()
        assert status(pending.id, root=root).state == "pending"
        inputs.rename(area / "removed-inputs")
        next_inputs.rename(area / "removed-next-inputs")
        for entry in (root / "bootstrap").iterdir():
            (entry / "inputs").rename(entry / "removed-source")
        run_slot(slot, "update_driver.foundation_inputs")
        run_slot(newer, "update_driver.foundation_inputs")
        run_slot(newer, "update_driver", "worker", "--root", str(root))
        assert status(pending.id, root=root).exit_code == 17
    finally:
        release.write_text("release\n")
        if old_worker is not None:
            if old_worker.poll() is None:
                os.killpg(old_worker.pid, signal.SIGKILL)
            old_worker.wait(timeout=10)
        if worker_log is not None:
            worker_log.close()
        for parent in parents:
            if parent.poll() is None:
                os.killpg(parent.pid, signal.SIGKILL)
            parent.wait(timeout=5)
        for descriptor in descriptors:
            if not _pidfd_exited(descriptor):
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
