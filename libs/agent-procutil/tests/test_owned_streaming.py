"""Confirmed POSIX groups retain the existing raw-output and clock owners."""

import subprocess
import sys
import time
import threading
import os

import pytest

from agent_procutil import owned_process as owned

pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(
        sys.platform not in ("linux", "darwin"), reason="native POSIX owned-group streaming",
    ),
]


def test_fd_output_is_live_before_command_completion(tmp_path):
    observed = []
    path, release = tmp_path / "output", tmp_path / "release"

    def receive():
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if path.read_bytes() == b"live":
                observed.append(True)
                release.touch()
                return
            time.sleep(.01)

    with path.open("wb") as output:
        reader = threading.Thread(target=receive)
        reader.start()
        try:
            result = owned.run_owned(
                [sys.executable, "-c",
                 "import os,sys,time;from pathlib import Path\n"
                 "os.write(1,b'live')\n"
                 "while not Path(sys.argv[1]).exists(): time.sleep(.01)\n",
                 str(release)],
                timeout=3, inactivity_timeout=1, capture=False,
                output_fds={"stdout": output.fileno()}, on_settled=lambda: None,
            )
            assert result.returncode == 0
        finally:
            reader.join(3)
    assert not reader.is_alive()
    assert observed == [True]


@pytest.mark.parametrize("absolute", [None, .25])
def test_raw_fd_progress_renews_only_inactivity(tmp_path, absolute):
    observed = []
    with (tmp_path / "output").open("w+b") as output:
        command = [
            sys.executable, "-c",
            "import os,time\nfor _ in range(10):\n os.write(1,b'x');time.sleep(.07)\n",
        ]
        call = lambda: owned.run_owned(
            command, timeout=absolute, inactivity_timeout=.2,
            output_fds={"stdout": output.fileno()}, capture=False,
            on_settled=lambda: observed.append(True),
        )
        started = time.monotonic()
        if absolute is None:
            assert call().returncode == 0
        else:
            with pytest.raises(subprocess.TimeoutExpired):
                call()
        assert time.monotonic() - started < 3
        output.seek(0)
        size = len(output.read())
        assert size == 10 if absolute is None else 0 < size < 10
    assert observed == [True]


def test_root_exit_does_not_skip_descendant_confirmation(tmp_path, monkeypatch):
    original = owned._observe_unreaped
    observations = []

    def observe(process):
        assert process.returncode is None
        observations.append(process.pid)
        return original(process)

    monkeypatch.setattr(owned, "_observe_unreaped", observe)
    with (tmp_path / "output").open("wb") as output:
        assert owned.run_owned(
            [sys.executable, "-c",
             "import subprocess,sys\nsubprocess.Popen([sys.executable,'-c',"
             "'import time;time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"],
            timeout=3, inactivity_timeout=1, capture=False,
            output_fds={"stdout": output.fileno()},
            on_settled=lambda: observations.append("settled"),
        ).returncode == 0
    assert observations[-1] == "settled"
    assert len(observations) > 2


def test_unconfirmed_cleanup_retains_custody_and_withholds_notification(tmp_path, monkeypatch):
    from agent_procutil import _caller_owned

    original = _caller_owned._confirm_group
    observed = []

    def fail(process, **kwargs):
        raise owned.OwnedCleanupError(process)

    monkeypatch.setattr(_caller_owned, "_confirm_group", fail)
    with (tmp_path / "output").open("wb") as output:
        with pytest.raises(owned.OwnedCleanupError) as raised:
            owned.run_owned(
                [sys.executable, "-c", "pass"], timeout=3, inactivity_timeout=1,
                output_fds={"stdout": output.fileno()}, capture=False,
                on_settled=lambda: observed.append(True),
            )
        process = raised.value.process
        assert process.returncode is None
        assert observed == []
        original(process)
        process.wait(timeout=2)
        for kind, resource in raised.value.cleanup_resources:
            assert kind == "output"
            resource.finish(2)
        for stream in (process.stdout, process.stderr):
            stream.close()


def test_cancellation_confirms_scope_before_preserving_exception(tmp_path, monkeypatch):
    from agent_procutil.streaming import SyncProcessOutput

    wait = SyncProcessOutput.wait
    interrupted, observed = [], []

    def interrupt(self, *args, **kwargs):
        if not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt
        return wait(self, *args, **kwargs)

    monkeypatch.setattr(SyncProcessOutput, "wait", interrupt)
    with (tmp_path / "output").open("wb") as output:
        with pytest.raises(KeyboardInterrupt):
            owned.run_owned(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                timeout=3, inactivity_timeout=1,
                output_fds={"stdout": output.fileno()}, capture=False,
                on_settled=lambda: observed.append(True),
            )
    assert observed == [True]


def test_output_startup_failure_does_not_notify_settlement(tmp_path, monkeypatch):
    from agent_procutil import streaming

    def fail(*args, **kwargs):
        raise RuntimeError("fixture output startup failed")

    monkeypatch.setattr(streaming, "SyncProcessOutput", fail)
    observed = []
    with (tmp_path / "output").open("wb") as output:
        with pytest.raises(owned.OwnedCleanupError) as raised:
            owned.run_owned(
                [sys.executable, "-c", "import time;time.sleep(30)"],
                timeout=3, inactivity_timeout=1,
                output_fds={"stdout": output.fileno()}, capture=False,
                on_settled=lambda: observed.append(True),
            )
        assert observed == []
        assert raised.value.process.returncode is not None
        for stream in (raised.value.process.stdout, raised.value.process.stderr):
            stream.close()


def test_text_is_decoded_only_after_complete_output_settlement(tmp_path, monkeypatch):
    from agent_procutil.streaming import SyncProcessOutput

    original = SyncProcessOutput.output

    def output(self):
        if not self._finished:
            raise UnicodeDecodeError("utf-8", b"\xe2\x82", 0, 2, "incomplete fixture character")
        return original(self)

    monkeypatch.setattr(SyncProcessOutput, "output", output)
    with (tmp_path / "output").open("wb") as stream:
        result = owned.run_owned(
            [sys.executable, "-c", "import os;os.write(1,b'\\xe2\\x82\\xac')"],
            timeout=3, inactivity_timeout=1,
            output_fds={"stdout": stream.fileno()}, on_settled=lambda: None,
        )
    assert result.stdout == "\u20ac"


def test_progressing_sink_cannot_renew_aggregate_settlement(tmp_path):
    from agent_procutil.owned_budget import OwnedExecutionBudget

    reader, writer = os.pipe()
    os.set_blocking(reader, False)
    stop, witnessed = threading.Event(), []

    def drain():
        while not stop.wait(.01):
            try:
                os.read(reader, 1024)
            except BlockingIOError:
                pass

    consumer = threading.Thread(target=drain)
    consumer.start()
    started = time.monotonic()
    try:
        with pytest.raises((owned.OwnedCleanupError, subprocess.TimeoutExpired)) as raised:
            owned.run_owned(
                [sys.executable, "-c", "import os;os.write(1,b'x'*524288)"],
                timeout=5, inactivity_timeout=1, capture=False,
                output_fds={"stdout": writer},
                budget=OwnedExecutionBudget.from_timeout(.6, cleanup_reserve=.4),
                on_settled=lambda: witnessed.append(time.monotonic()),
            )
        assert time.monotonic() - started < 2
        assert not witnessed
        process = getattr(raised.value, "process", None)
        if process is not None and process.returncode is None:
            from agent_procutil import _caller_owned

            owned._retire_posix_group(process)
            _caller_owned._confirm_group(process)
            process.wait(timeout=2)
        for kind, resource in getattr(raised.value, "cleanup_resources", ()):
            if kind == "output":
                resource.finish(2, stop_at_idle=True)
    finally:
        stop.set()
        consumer.join(2)
        os.close(reader)
        os.close(writer)
    assert not consumer.is_alive()


def test_partial_output_initialization_cleanup_uses_same_deadline(tmp_path, monkeypatch):
    from agent_procutil import _output_relay, _caller_owned
    from agent_procutil.owned_budget import OwnedExecutionBudget

    created, allowances, witnessed = [], [], []

    class PartialRelay:
        def abort(self, timeout):
            allowances.append(timeout)
            time.sleep(timeout)

    def relay(*args, **kwargs):
        created.append(True)
        if len(created) == 2:
            raise OSError("fixture second relay unavailable")
        return PartialRelay()

    monkeypatch.setattr(_output_relay, "ByteRelay", relay)
    marker = tmp_path / "late-write"
    with (tmp_path / "output").open("wb") as stream:
        started = time.monotonic()
        with pytest.raises(owned.OwnedCleanupError) as raised:
            owned.run_owned(
                [sys.executable, "-c",
                 "import time,sys;from pathlib import Path;time.sleep(2.5);Path(sys.argv[1]).touch()",
                 str(marker)],
                timeout=5, inactivity_timeout=1, capture=False,
                output_fds={"stdout": stream.fileno(), "stderr": stream.fileno()},
                budget=OwnedExecutionBudget.from_timeout(1, cleanup_reserve=.5),
                on_settled=lambda: witnessed.append(True),
            )
        assert time.monotonic() - started < 1.8
        assert len(allowances) == 1 and allowances[0] <= 1
        assert not witnessed and not marker.exists()
        process = raised.value.process
        if process.returncode is None:
            owned._retire_posix_group(process)
            _caller_owned._confirm_group(process)
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
