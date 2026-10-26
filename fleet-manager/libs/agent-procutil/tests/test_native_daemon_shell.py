"""Native launch survives the caller's real process group; contained tests do not escape."""

import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

import pytest

from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native daemon admission")


@pytest.mark.parametrize("disposable", [False, True])
def test_native_daemon_waitable_child_preserves_real_exit_status(tmp_path, disposable):
    from contextlib import nullcontext
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    source = Path(__file__).parents[1] / "src/agent_procutil"
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    for name in ("native_daemon.sh", "daemon_instances.sh", "_build_native.py", "daemon_instance_native.c"):
        shutil.copy2(source / name, helpers / name)
    artifact = helpers / "native" / f"linux-{platform.machine()}" / "daemon-instance"
    built = subprocess.run(
        [sys.executable, str(helpers / "_build_native.py"), "--output", str(artifact)],
        capture_output=True, text=True, timeout=90,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    worker = tmp_path / "worker.sh"
    worker.write_text("#!/usr/bin/env bash\nsleep .1\nexit 37\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env.pop("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", None)
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
def test_native_daemon_and_bounded_control_after_caller_group_destruction(tmp_path, contained):
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
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            os.close(descriptor)
