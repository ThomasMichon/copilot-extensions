import asyncio
import json
import signal
import sys
from types import SimpleNamespace

import pytest
from agent_procutil.native_child import NativeChildCleanupError
from agent_procutil.owned_process import run_caller_owned
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_ssh_service import hosting
from agent_ssh_service.__main__ import main


@pytest.mark.parametrize("custody_pending", [False, True])
def test_cli_retains_real_writer_lease_until_original_native_custody_settles(
    config, tmp_path, monkeypatch, capsys, custody_pending,
):
    lease = SingleInstance(str(tmp_path), service="agent-ssh-service")
    contender = SingleInstance(str(tmp_path), service="agent-ssh-service")
    lease.acquire()
    selected = tmp_path / "service.json"
    selected.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    attempts, waits = [], []
    sleep = asyncio.sleep

    class Custody:
        def retire(self):
            attempts.append(True)
            assert lease.held
            with pytest.raises(AlreadyRunningError):
                contender.acquire()
            if len(attempts) <= 3:
                raise NativeChildCleanupError(self)
            return 0

    custody = Custody()
    relay = SimpleNamespace(
        process=SimpleNamespace(returncode=None),
        custody=None if custody_pending else custody,
    )

    async def failed_run(_config):
        previous = {selected_signal: signal.signal(selected_signal, lambda *_: None)
                    for selected_signal in (signal.SIGINT, signal.SIGTERM)}
        raise hosting.UnsettledRelay(relay, lease, previous)

    async def cancelled_wait(delay):
        waits.append(delay)
        if relay.custody is None:
            assert lease.held
            with pytest.raises(AlreadyRunningError):
                contender.acquire()
            relay.custody = custody
        for selected_signal in (signal.SIGINT, signal.SIGTERM):
            signal.getsignal(selected_signal)(selected_signal, None)
        asyncio.current_task().cancel()
        await sleep(0)

    monkeypatch.setattr(hosting, "run", failed_run)
    monkeypatch.setattr(asyncio, "sleep", cancelled_wait)
    try:
        assert main(["--config", str(selected)]) == 1
        assert len(attempts) == 4 and waits == [1] * (4 if custody_pending else 3)
        assert relay.process.returncode == 0 and not lease.held
        contender.acquire()
        assert "foreground custody and writer lease retained" in capsys.readouterr().err
    finally:
        contender.release()
        lease.release()


def test_real_sigterm_at_actual_hosting_restore_keeps_cli_and_writer_custody(
    config, tmp_path,
):
    from test_relay import relay_document

    selected = tmp_path / "signal-service.json"
    selected.write_text(json.dumps(relay_document(config)), encoding="utf-8")
    script = r"""
import asyncio, signal, sys
from types import SimpleNamespace
from single_instance_lease import AlreadyRunningError, SingleInstance
from agent_procutil.native_child import NativeChildCleanupError
from agent_ssh_service import hosting, relay
from agent_ssh_service.__main__ import main

phase = {"unsettled": False, "injected": False, "retirements": 0}
original_term = signal.getsignal(signal.SIGTERM)
real_signal = signal.signal

def observing_signal(number, handler):
    previous = real_signal(number, handler)
    if (number == signal.SIGTERM and phase["unsettled"]
            and phase["retirements"] == 1 and not phase["injected"]):
        phase["injected"] = True
        signal.raise_signal(signal.SIGTERM)
    return previous

signal.signal = observing_signal
hosting.load_service_credentials = lambda **_: SimpleNamespace(access_token=None, proof_key=None)

class Custody:
    def __init__(self, root):
        self.root = root

    def retire(self):
        phase["retirements"] += 1
        contender = SingleInstance(str(self.root), service="agent-ssh-service")
        try:
            contender.acquire()
        except AlreadyRunningError:
            pass
        else:
            contender.release()
            raise AssertionError("writer custody was released before native settlement")
        if phase["retirements"] == 2 and not phase["injected"]:
            phase["injected"] = True
            signal.raise_signal(signal.SIGTERM)
        if phase["retirements"] == 1:
            raise NativeChildCleanupError(self)
        return 0

class Relay:
    def __init__(self, store):
        self.process = SimpleNamespace(returncode=None)
        self.custody = Custody(store.root)

    def reconcile(self):
        pass

    async def run(self, stop, ready):
        ready.set()
        await stop.wait()
        try:
            self.process.returncode = self.custody.retire()
        finally:
            phase["unsettled"] = True

class Connector:
    def __init__(self, **_):
        pass

    async def run(self, stop):
        stop.set()

relay.NativeRelay = Relay
hosting.ServiceConnector = Connector
assert main(["--config", sys.argv[1]]) == 1
assert phase["injected"] and phase["retirements"] == 2
assert signal.getsignal(signal.SIGTERM) == original_term
print("signal-handoff-retained")
"""
    result = run_caller_owned(
        [sys.executable, "-I", "-c", script, str(selected)],
        timeout=20, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "signal-handoff-retained"
