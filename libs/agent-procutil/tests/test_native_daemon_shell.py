"""Native launch survives the caller's real process group; contained tests do not escape."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd, send_pidfd_signal

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native daemon admission")


@pytest.mark.parametrize("disposable", [False, True])
def test_native_daemon_waitable_child_preserves_real_exit_status(tmp_path: Path, disposable: bool) -> None:
    from contextlib import nullcontext
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    source = Path(__file__).parents[1] / "src/agent_procutil"
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    for name in ("native_daemon.sh", "daemon_instances.sh"):
        shutil.copy2(source / name, helpers / name)
    assets = importlib.import_module("daemon_lifecycle_assets")
    archive = helpers / assets.ARCHIVE_NAME
    with archive.open("xb") as stream:
        archive.chmod(0o600)
        stream.write(assets.lifecycle_archive(Path(__file__).parents[3]))
    worker = tmp_path / "worker.sh"
    worker.write_text("#!/usr/bin/env bash\nsleep .1\nexit 37\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env.pop("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", None)
    env["COPILOT_EXTENSIONS_DAEMON_PYTHON"] = "/usr/bin/python3"
    lifecycle = daemon_test_lifecycle(
        owner="native-wait-test", registry_root=tmp_path / "registry",
    ) if disposable else nullcontext({})
    with lifecycle as declaration:
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c",
             'set -euo pipefail; source "$1"; '
             'start_native_daemon "$2" "$3" "$3/daemon.log"; '
             'printf "%s %s\\n" "$__native_daemon_pid" "$__native_daemon_wait_pid"; '
             'code=0; wait "$__native_daemon_wait_pid" || code=$?; exit "$code"',
             "native-wait", str(helpers / "native_daemon.sh"), str(worker), str(tmp_path)],
            env={**env, **declaration}, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 37, result.stdout + result.stderr
        actual, waitable = map(int, result.stdout.split())
        assert (actual != waitable) is disposable


@pytest.mark.parametrize("contained", [False, True])
def test_native_daemon_and_bounded_control_after_caller_group_destruction(tmp_path: Path, contained: bool) -> None:
    if sys.platform == "win32":
        pytest.skip("Linux process-group and pidfd control")
    tests = Path(__file__).parent
    helper = tests.parent / "src" / "agent_procutil" / "native_daemon.sh"
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    if contained:
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    parent = subprocess.Popen(
        ["bash", str(tests / "fixture_native_daemon.sh"), str(helper), str(tmp_path)],
        start_new_session=True, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    descriptors = []
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / name).is_file() for name in ("daemon", "control")):
            assert parent.poll() is None, (tmp_path / "daemon.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(.01)
        daemon = int((tmp_path / "daemon").read_text())
        control = int((tmp_path / "control").read_text())
        descriptors = [open_pidfd(daemon), open_pidfd(control)]
        assert (os.getsid(daemon) == parent.pid) == contained
        assert os.getsid(control) == parent.pid
        os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 10
        while not _pidfd_exited(descriptors[1]) or (contained and not _pidfd_exited(descriptors[0])):
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert _pidfd_exited(descriptors[0]) == contained
        assert not list(tmp_path.glob(".native-daemon-*"))
    finally:
        if parent.poll() is None:
            os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        for descriptor in descriptors:
            if not _pidfd_exited(descriptor):
                send_pidfd_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)


@pytest.mark.parametrize("expired", [False, True])
def test_native_admission_deadline_does_not_retire_independent_owner(tmp_path: Path, expired: bool) -> None:
    source = Path(__file__).parents[1] / "src/agent_procutil/native_daemon.sh"
    helper = tmp_path / source.name
    shutil.copyfile(source, helper)
    (tmp_path / "daemon_instances.sh").write_text(
        'new_daemon_instance_reservation() { __daemon_instance_id=fixture; '
        '__daemon_instance_root="$2"; __daemon_instance_record="$2/fixture"; }\n'
        '_di_read() { sleep 1; return 1; }\n',
        encoding="utf-8",
    )
    worker = tmp_path / "worker.sh"
    worker.write_text("exit 99\n", encoding="utf-8")
    environment = {**os.environ, "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"}
    result = subprocess.run(
        ["bash", "-c",
         'source "$1"; read -r now _ </proc/uptime; '
         'deadline="$(awk -v now="$now" -v expired="$4" '
         "'BEGIN { printf \"%.3f\", now+(expired ? -1 : .2) }')\"; "
         'code=0; start_native_daemon --admission-deadline "$deadline" "$2" "$3" "$3/log" || code=$?; '
         '[[ "$code" != 0 ]] || exit 91; '
         'if [[ "$4" == 0 ]]; then '
         'kill -0 "$__native_daemon_wait_pid" || exit 92; '
         'printf "owner-still-running\\n"; wait "$__native_daemon_wait_pid"; '
         'else [[ ! -v __native_daemon_wait_pid ]] || exit 93; fi',
         "deadline-fixture", str(helper), str(worker), str(tmp_path), str(int(expired))],
        env=environment, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == (0 if expired else 1), result.stdout + result.stderr
    assert ("owner-still-running" in result.stdout) != expired
    assert not list(tmp_path.glob(".native-daemon-*"))
    assert "Native daemon admission" in result.stderr
