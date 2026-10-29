"""Native rollback after abrupt caller death, without caller-side cleanup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import zdd

import agent_procutil as pu
from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil.daemon_instances import (
    DaemonInstanceReceipt,
    cleanup_instance,
    daemon_test_lifecycle,
)
from agent_procutil.process_retirement import PinnedProcess, RetirementRefused
from agent_procutil.retirement_custody import RetirementCustody

pytestmark = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="Retirement custody requires Windows or Linux",
)

_OWNER = """
import sys
sys.path[:0] = sys.argv[1:3]
del sys.argv[1:3]
import json,os,time
from pathlib import Path
from agent_procutil.retirement_custody import RetirementCustody
from agent_procutil._daemon_instance_identity import birth_identity
with RetirementCustody(Path(sys.argv[1])) as custody:
    pin = custody.pin(int(sys.argv[2]), sys.argv[3])
    pin.pause()
    process = custody._process
    print(json.dumps({
        'pid': process.pid, 'birth': birth_identity(process.pid),
        'instance': process.instance_receipt.instance_id,
        'registry': str(process.instance_receipt.registry_root),
    }), flush=True)
    time.sleep(45)
"""
_TARGET = (
    "import os,sys,time\n"
    "with open(sys.argv[1],'ab',buffering=0) as stream:\n"
    " while True:\n"
    "  stream.write(b'.')\n"
    "  time.sleep(.01)\n"
)


def _progress(path: Path, previous: int = 0) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > previous:
            return path.stat().st_size
        time.sleep(.01)
    raise AssertionError("Original target did not make progress")


@pytest.fixture
def lifetime(tmp_path, monkeypatch):
    with daemon_test_lifecycle(
        owner="retirement-custody-test", registry_root=tmp_path / "ledger",
    ) as env:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        yield


def test_native_proxy_rollback_and_confirmed_exit(tmp_path, lifetime):
    progress = tmp_path / "progress"
    target = subprocess.Popen(  # noqa: S603 - owned native fixture interpreter
        [getattr(sys, "_base_executable", sys.executable), "-c", _TARGET, str(progress)],
        **pu.no_window_kwargs(),
    )
    try:
        _progress(progress)
        with RetirementCustody(tmp_path) as custody:
            actual = custody._process
            with pytest.raises(RetirementRefused, match="refused native"):
                custody.pin(target.pid, birth_identity(target.pid) + "1")
            with custody.pin(target.pid, birth_identity(target.pid)) as pin:
                pin.pause()
                before = progress.stat().st_size
                time.sleep(.05)
                assert progress.stat().st_size == before
                assert not pin.exited()
            _progress(progress, before)
            with custody.pin(target.pid, birth_identity(target.pid)) as pin:
                pin.pause()
                pin.terminate()
                assert pin.exited()
            assert target.wait(timeout=5) is not None
        assert actual.poll() is not None
        assert not actual.instance_receipt.record_path.exists()
    finally:
        if target.poll() is None:
            target.kill()
            target.wait(timeout=5)


def test_killed_owner_after_first_native_pause_resumes_original(tmp_path, lifetime):
    progress = tmp_path / "progress"
    target = subprocess.Popen(  # noqa: S603 - owned native fixture interpreter
        [getattr(sys, "_base_executable", sys.executable), "-c", _TARGET, str(progress)],
        **pu.no_window_kwargs(),
    )
    owner = None
    guardian = None
    try:
        _progress(progress)
        python, environment = pu.direct_python(sys.executable)
        owner = subprocess.Popen(  # noqa: S603 - owned native fixture interpreter
            [
                python, "-I", "-c", _OWNER,
                str(Path(pu.__file__).resolve().parent.parent),
                str(Path(zdd.__file__).resolve().parent.parent),
                str(tmp_path), str(target.pid), birth_identity(target.pid),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, **environment},
            **pu.no_window_kwargs(),
        )
        offer = json.loads(owner.stdout.readline())
        guardian = PinnedProcess(offer["pid"], offer["birth"])
        before = progress.stat().st_size
        time.sleep(.05)
        assert progress.stat().st_size == before
        owner.kill()
        owner.wait(timeout=5)
        assert _progress(progress, before) > before
        assert target.poll() is None
        assert guardian.exited(5)
        receipt = DaemonInstanceReceipt(offer["instance"], Path(offer["registry"]))
        assert cleanup_instance(receipt, terminate=False).status == "settled"
        assert birth_identity(os.getpid())
    finally:
        if owner is not None and owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if target.poll() is None:
            target.kill()
            target.wait(timeout=5)
        if guardian is not None:
            assert guardian.exited(5)
            guardian.close()
        if owner is not None:
            owner.stdout.close()
            owner.stderr.close()


def test_ambiguous_resume_is_not_retried(monkeypatch):
    from zdd.private_protocol import PrivateIPCError, Refusal

    from agent_procutil import _retirement_custodian as worker
    from agent_procutil.process_retirement import RetirementRefused

    calls = []

    class Pin:
        def __init__(self, *_):
            pass

        def close(self):
            calls.append("close")
            if calls == ["close"]:
                raise RetirementRefused("ambiguous resume")

        def exited(self, *_):
            calls.append("exit")
            return True

    requests = iter([
        {"operation": "pin", "pid": 42, "birth": "original"},
        {"operation": "close", "pid": 42},
    ])

    class Channel:
        def check_peer(self, *_):
            pass

        def close(self):
            pass

    def read(*_):
        try:
            return next(requests)
        except StopIteration:
            raise PrivateIPCError(Refusal.CLOSED) from None

    monkeypatch.setattr(worker, "PinnedProcess", Pin)
    monkeypatch.setattr(worker, "read_frame", read)
    monkeypatch.setattr(worker, "write_frame", lambda *_: None)
    worker.serve(Channel(), 41)
    assert calls == ["close", "exit", "close"]


def test_lost_owner_cannot_dispatch_buffered_termination(monkeypatch):
    from zdd.private_protocol import PrivateIPCError, Refusal

    from agent_procutil import _retirement_custodian as worker

    effects = []
    requests = iter([
        {"operation": "pin", "pid": 42, "birth": "original"},
        {"operation": "pause", "pid": 42},
        {"operation": "terminate", "pid": 42},
    ])

    class Pin:
        def __init__(self, *_):
            pass

        def pause(self):
            effects.append("pause")

        def terminate(self):
            effects.append("terminate")

        def close(self):
            effects.append("resume")

    class Channel:
        checks = 0

        def check_peer(self, *_):
            self.checks += 1
            if self.checks == 3:
                raise PrivateIPCError(Refusal.CLOSED)

        def close(self):
            pass

    monkeypatch.setattr(worker, "PinnedProcess", Pin)
    monkeypatch.setattr(worker, "read_frame", lambda *_: next(requests))
    monkeypatch.setattr(worker, "write_frame", lambda *_: None)
    worker.serve(Channel(), 41)
    assert effects == ["pause", "resume"]


def test_failed_channel_close_still_retains_unsettled_worker(tmp_path):
    class Channel:
        def close(self):
            raise OSError("native close failed")

    class Process:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("owned-custodian", timeout)

    custody = RetirementCustody(tmp_path)
    custody._channel = Channel()
    custody._process = Process()
    with pytest.raises(RetirementRefused, match="retains unsettled") as caught:
        custody.close()
    assert caught.value.process is custody._process
