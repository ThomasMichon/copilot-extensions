"""Explicit cold native handoff to the canonical ordinary-request queue."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest
import agent_procutil
from update_driver import foundation_status, status
from update_driver.foundation_inputs import GENERATED, PACKAGES

pytestmark = pytest.mark.skipif(
    not (
        (os.name == "nt" and os.environ.get("UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS") == "1")
        or (sys.platform == "linux" and os.environ.get("UPDATE_DRIVER_NATIVE_LINUX_GENESIS") == "1")
    ),
    reason="explicit private cold downloads and native caller-loss qualification",
)


def observe(predicate, timeout, diagnostic):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError(diagnostic())


@pytest.mark.parametrize("payload_fixture", [
    "owning_payload", "owning_pull_requests", "owning_ssh", "owning_codespaces", "owning_logger",
])
def test_native_cold_first_use_survives_caller_loss_and_keeps_ordinary_result(
    request, payload_fixture, area, native_window_observation,
):
    payload, installation, project = request.getfixturevalue(payload_fixture)
    library = Path(__file__).resolve().parents[2]
    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(
            library / name, inputs / name,
            ignore=lambda _path, names: {
                name for name in names if name in GENERATED
                or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
            },
        )
    source = inputs / "update-driver"
    root = area / "cold"
    # Hold the fixed native product invocation after it has entered the queue's tree.
    windows = payload / "scripts" / "install.ps1"
    windows.write_text(windows.read_text().replace(
        "exit 7", "while (-not (Test-Path (Join-Path $InstallDir 'release'))) { Start-Sleep -Milliseconds 100 }\nexit 7",
    ), encoding="utf-8")
    posix = payload / "scripts" / "install.sh"
    posix.write_text(posix.read_text().replace(
        "exit 7", 'while [[ ! -f "$3/release" ]]; do sleep .1; done\nexit 7',
    ), encoding="utf-8")
    env = dict(os.environ, UV_PYTHON_PREFERENCE="only-managed", UV_PYTHON_DOWNLOADS="automatic")
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env.pop("UPDATE_DRIVER_WITNESS", None)
    caller_log = (area / "native-caller.log").open("w")
    job = None
    supervisor = None
    caller = None
    descriptor = None

    def diagnostic():
        paths = [area / "native-caller.log", *root.glob("*.log")]
        return "\n".join(
            f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}" for path in paths
        )

    try:
        if os.name == "nt":
            from test_foundation_genesis_process import pinned_worker
            shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
            caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
                [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                 str(source / "scripts" / "first-use.ps1"), "-Root", str(root),
                 "-Payload", str(payload), "-RuntimeRoot", str(installation),
                 "-Project", str(project)],
                env=env, require_job=True, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=caller_log, stderr=caller_log,
                **agent_procutil.no_window_kwargs(),
            )
            assert caller.wait(timeout=90) == 0, diagnostic()
            frame = json.loads((area / "native-caller.log").read_text().strip())
            supervisor = pinned_worker(frame)
        else:
            from agent_procutil._caller_owned import _pidfd_exited
            from agent_procutil._linux_pidfd import open_pidfd
            tools = area / "native-tools"
            tools.mkdir()
            for name in (
                "bash", "dirname", "basename", "uname", "readlink", "stat", "find", "sort",
                "sha256sum", "iconv", "cat", "mkdir", "sync", "awk", "date", "mktemp", "mv",
                "rm", "cp", "flock", "grep", "sleep", "setsid", "mkfifo", "rmdir", "curl",
                "tar", "gzip", "head", "chmod", "sed", "tr", "ps", "realpath", "cut",
                "wc", "od", "id", "getent", "hostname",
            ):
                executable = shutil.which(name)
                assert executable, f"native tool unavailable: {name}"
                (tools / name).symlink_to(executable)
            env["PATH"] = str(tools)
            caller = subprocess.Popen(
                [str(tools / "bash"), "-c", '"$@" ; code=$?; [[ "$code" == 0 ]] || exit "$code"; sleep 900',
                 "first-use-caller", str(tools / "bash"), str(source / "scripts" / "first-use.sh"),
                 str(root), str(payload), str(installation), str(project)],
                env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=caller_log, stderr=caller_log,
            )
            observe(
                lambda: "launched:" in (area / "native-caller.log").read_text() or caller.poll() is not None,
                90, diagnostic,
            )
            frame = (area / "native-caller.log").read_text().strip()
            assert frame.startswith("launched:"), diagnostic()
            descriptor = open_pidfd(int(frame.split()[0].partition(":")[2]))
        observe(lambda: foundation_status(root=root) is not None, 90, diagnostic)
        operation = foundation_status(root=root)
        assert operation.state == "running", diagnostic()
        # Destroy the originating caller's entire cleanup boundary during acquisition.
        if job is not None:
            job.close()
            job = None
            assert supervisor.poll() is None, diagnostic()
        else:
            os.killpg(caller.pid, signal.SIGKILL)
            caller.wait(timeout=5)
            assert not _pidfd_exited(descriptor), diagnostic()
        observe(
            lambda: (installation / "source-path").exists()
            or (supervisor.poll() is not None if supervisor is not None else _pidfd_exited(descriptor))
            or foundation_status(root=root).state in {"failed", "interrupted"},
            600, diagnostic,
        )
        assert (installation / "executed").exists(), diagnostic()
        requests = status(root=root)
        assert len(requests) == 1 and requests[0].state == "running", diagnostic()
        request = requests[0]
        assert request.spec.backend == "owning-plugin-provision"
        # The old native snapshot is not the receipt for this ordinary transaction.
        assert foundation_status(root=root).state == "completed"
        assert foundation_status(root=root).operation_id == operation.operation_id
        frozen = Path((installation / "source-path").read_text().strip()).parent
        if payload_fixture in {"owning_pull_requests", "owning_ssh", "owning_codespaces", "owning_logger"}:
            assert frozen.parent.parent == installation / ".install-stage"
            assert frozen.parent.name.startswith(request.id + "-")
        else:
            assert frozen == root / "provision-inputs" / request.id
        # The admitted native program uses retained inputs, not the mutable payload.
        (payload / "scripts" / "install.ps1").write_text("exit 99\n", encoding="utf-8")
        (payload / "scripts" / "install.sh").write_text("exit 99\n", encoding="utf-8")
        (installation / "release").write_text("release\n")
        observe(lambda: status(request.id, root=root).state != "running", 60, diagnostic)
        result = status(request.id, root=root)
        assert result.state == "completed" and result.exit_code == 7, diagnostic()
        receipts = [
            json.loads(line) for path in root.glob("first-use-*.log")
            for line in path.read_text(errors="replace").splitlines()
            if line.startswith('{"request_id"')
        ]
        assert receipts == [{"request_id": request.id, "state": "pending"}], diagnostic()
        if supervisor is not None:
            assert supervisor.wait(timeout=60) == 7, diagnostic()
    finally:
        installation.mkdir(exist_ok=True)
        (installation / "release").write_text("release\n")
        if job is not None:
            job.close()
        if caller is not None and caller.poll() is None:
            if os.name == "nt":
                caller.kill()
            else:
                os.killpg(caller.pid, signal.SIGKILL)
            caller.wait(timeout=10)
        if supervisor is not None:
            supervisor.wait(timeout=900)
            supervisor.close()
        if descriptor is not None:
            try:
                observe(lambda: _pidfd_exited(descriptor), 900, diagnostic)
            finally:
                os.close(descriptor)
        caller_log.close()
