"""Real Linux child custody, including a Bash parent deliberately execing Python."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil import native_child
from agent_procutil._caller_owned import _pidfd_exited
from agent_procutil._linux_pidfd import open_pidfd
from agent_procutil.native_child import NativeChildCleanupError, NativeChildCustody
from agent_procutil.owned_process import run_owned

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native child custody")


@pytest.fixture
def child():
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time; time.sleep(300)"],
        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    custody = NativeChildCustody(process.pid)
    try:
        yield process, custody
    finally:
        custody.retire()
        process.returncode = custody.returncode


def test_native_exec_handoff_retires_descendants_before_reaping(tmp_path):
    tests = Path(__file__).parent
    source = tests.parent / "src"
    result = run_owned(
        [
            "bash", str(tests / "fixture_native_child.sh"), sys.executable, str(source),
            str(tmp_path), str(tests / "fixture_native_child.py"),
        ],
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["settled"] is True


def test_actual_child_is_pinned_until_whole_group_settlement(child):
    process, custody = child
    custody.assert_owned()
    assert custody.returncode is None and custody.pid == process.pid
    assert custody.retire() == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    with pytest.raises(RuntimeError, match="released"):
        custody.assert_owned()


def test_already_exited_unreaped_root_retains_its_live_descendants():
    process = subprocess.Popen(
        [
            sys.executable, "-I", "-S", "-c",
            "import subprocess,sys;"
            "p=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(300)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "print(p.pid,flush=True);raise SystemExit(17)",
        ],
        start_new_session=True, stdout=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    custody = NativeChildCustody(process.pid)
    descriptor = None
    try:
        descendant = int(process.stdout.readline())
        descriptor = open_pidfd(descendant)
        deadline = time.monotonic() + 10
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert not _pidfd_exited(descriptor)
        assert custody.retire() == 17
        assert _pidfd_exited(descriptor)
    finally:
        custody.retire()
        process.returncode = custody.returncode
        process.stdout.close()
        if descriptor is not None:
            os.close(descriptor)


def test_uncertain_cleanup_preserves_the_same_unreaped_owner(child, monkeypatch):
    process, custody = child
    original = native_child._confirm_group

    def unavailable(_root):
        raise RuntimeError("test group observation unavailable")

    monkeypatch.setattr(native_child, "_confirm_group", unavailable)
    with pytest.raises(NativeChildCleanupError) as caught:
        custody.retire()
    assert caught.value.custody is custody
    assert custody.returncode is None and custody._descriptor >= 0
    custody.assert_owned()
    monkeypatch.setattr(native_child, "_confirm_group", original)
    assert custody.retire() == -signal.SIGKILL
    process.returncode = custody.returncode


def test_other_thread_cannot_signal_or_release_adopted_custody(child):
    _process, custody = child
    errors = []

    def foreign_thread():
        for method in (custody.assert_owned, custody.retire):
            try:
                method()
            except RuntimeError as error:
                errors.append(str(error))

    thread = threading.Thread(target=foreign_thread)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 2 and all("process and thread" in error for error in errors)
    custody.assert_owned()


def test_missing_kernel_identity_never_signals_the_real_child(child, monkeypatch):
    process, custody = child
    with monkeypatch.context() as patch:
        patch.setattr(native_child, "_pidfd_pid", lambda _descriptor: -1)
        with pytest.raises(NativeChildCleanupError):
            custody.retire()
        assert os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
    custody.assert_owned()


def test_fork_does_not_transfer_parent_child_authority(child):
    _process, custody = child
    forked = os.fork()
    if forked == 0:
        try:
            custody.retire()
        except RuntimeError:
            os._exit(0)
        os._exit(1)
    waited, status = os.waitpid(forked, 0)
    assert waited == forked and os.waitstatus_to_exitcode(status) == 0
    custody.assert_owned()


def test_nonchild_and_reaped_child_refusals_do_not_leak_descriptors():
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(RuntimeError, match="unconfirmed"):
        NativeChildCustody(os.getpid())
    process = subprocess.Popen([sys.executable, "-I", "-S", "-c", "pass"], start_new_session=True)
    process.wait(timeout=10)
    with pytest.raises((OSError, RuntimeError)):
        NativeChildCustody(process.pid)
    assert len(os.listdir("/proc/self/fd")) == before


def test_non_session_child_refuses_without_signalling():
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time; time.sleep(300)"],
    )
    try:
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(RuntimeError, match="session and process group"):
            NativeChildCustody(process.pid)
        assert len(os.listdir("/proc/self/fd")) == before
        assert os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
    finally:
        # The fixture retains its original unreaped Popen child through signalling.
        process.kill()
        process.wait(timeout=10)


def test_duplicate_adoption_does_not_create_another_reaper(child):
    process, custody = child
    with pytest.raises(RuntimeError, match="already adopted"):
        NativeChildCustody(process.pid)
    custody.assert_owned()


def test_incompatible_sigchld_refuses_without_consuming_native_child():
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time; time.sleep(300)"],
        start_new_session=True,
    )
    previous = signal.signal(signal.SIGCHLD, lambda *_args: None)
    before = len(os.listdir("/proc/self/fd"))
    try:
        with pytest.raises(RuntimeError, match="SIGCHLD"):
            NativeChildCustody(process.pid)
        assert len(os.listdir("/proc/self/fd")) == before
    finally:
        signal.signal(signal.SIGCHLD, previous)
        custody = NativeChildCustody(process.pid)
        custody.retire()
        process.returncode = custody.returncode


@pytest.mark.parametrize("pid", [True, 0, -1, "1", None])
def test_native_child_rejects_invalid_identities_before_kernel_access(pid):
    with pytest.raises(ValueError, match="positive integer"):
        NativeChildCustody(pid)
