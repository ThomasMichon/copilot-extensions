import asyncio
import json
import signal
from types import SimpleNamespace

import pytest
from agent_procutil.native_child import NativeChildCleanupError
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
        raise hosting.UnsettledRelay(relay, lease)

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
