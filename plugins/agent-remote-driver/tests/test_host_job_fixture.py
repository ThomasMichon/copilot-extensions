"""Fixture deadlines cover admission, unrelated frames and relative paths."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fixture_module():
    path = Path(__file__).with_name("native") / "host_caller_jobs_fixture.py"
    spec = importlib.util.spec_from_file_location("owned_host_job_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relative_output_is_absolute_before_native_dispatch_and_lease_is_bounded(
    tmp_path, monkeypatch, fixture_module,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fixture_module.sys, "platform", "win32")
    monkeypatch.setattr(fixture_module.pu, "contained_test_mode", lambda: False)
    observed = []

    def lifecycle(**kwargs):
        observed.append(kwargs)
        assert kwargs["registry_root"] == tmp_path / "relative-output" / "instances"
        lease = kwargs["lease"]
        assert 0 < lease.ttl_seconds <= lease.max_lifetime_seconds < 180
        raise RuntimeError("captured before native dispatch")

    monkeypatch.setattr(fixture_module, "daemon_test_lifecycle", lifecycle)
    with pytest.raises(RuntimeError, match="captured before native dispatch"):
        fixture_module.qualify(Path("relative-output"))
    assert len(observed) == 1


@pytest.mark.parametrize("blocked", ["attach", "unrelated-frames"])
def test_broker_probe_has_one_complete_exchange_deadline_and_bounded_cleanup(
    monkeypatch, fixture_module, blocked,
):
    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    async def frames():
        while True:
            await asyncio.sleep(0)
            yield 1, b'{"jsonrpc":"2.0","id":999,"result":{}}'

    client = SimpleNamespace(
        attach=AsyncMock(side_effect=stalled) if blocked == "attach" else
        AsyncMock(return_value=SimpleNamespace(child_pid=123)),
        write=AsyncMock(), ack=AsyncMock(), frames=frames,
        terminate=AsyncMock(), close=AsyncMock(),
    )
    monkeypatch.setattr(
        fixture_module.SessionHostClient, "connect", AsyncMock(return_value=client),
    )
    wait_for = asyncio.wait_for
    allowances = []

    async def short_bound(awaitable, timeout):
        allowances.append(timeout)
        return await wait_for(awaitable, .01)

    monkeypatch.setattr(fixture_module.asyncio, "wait_for", short_bound)
    result = {"port": 1, "nonce": "synthetic", "child": {"pid": 123}}

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await wait_for(fixture_module._probe(result), .5)

    asyncio.run(run())
    assert allowances and allowances.count(10) == 1
    client.close.assert_awaited_once()
    if blocked == "attach":
        client.terminate.assert_not_awaited()
    else:
        client.terminate.assert_awaited_once()
