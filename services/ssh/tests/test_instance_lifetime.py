"""The foreground retains admission across service and original-relay settlement."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agent_procutil import daemon_instances as instances

from agent_ssh_service import hosting
from agent_ssh_service.__main__ import _foreground


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_fresh_metadata_does_not_load_process_admission(flag):
    import sys
    from pathlib import Path

    from agent_procutil.owned_process import run_caller_owned

    project = Path(__file__).resolve().parents[1]
    script = """
import builtins,runpy,sys
from pathlib import Path
project=Path(sys.argv[1])
sys.path[:0]=[str(project/'src'),str(project.parents[1]/'libs/agent-procutil/src')]
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'asyncio','sqlite3','subprocess','fleet_manager','single_instance_lease'}:
        raise AssertionError('runtime dependency imported for metadata: '+name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
sys.argv=['agent-ssh-service',sys.argv[2]]
runpy.run_module('agent_ssh_service',run_name='__main__')
"""
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-c", script, str(project), flag],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    if flag == "--help":
        assert "--config" in result.stdout
    else:
        from agent_ssh_service import __version__

        assert result.stdout.strip() == __version__


def observe_admission(monkeypatch):
    acquired = []
    admit = instances.admit_daemon_instance

    def observe(*args, **kwargs):
        guard = admit(*args, **kwargs)
        acquired.append(guard)
        return guard

    monkeypatch.setattr(instances, "admit_daemon_instance", observe)
    return acquired


def record(guard):
    return next(
        row for row in instances.inspect_instances(guard.receipt.registry_root)
        if row["instance_id"] == guard.receipt.instance_id
    )


@pytest.mark.parametrize("failure", [False, True])
async def test_foreground_admits_before_service_and_finishes_after_terminal_work(
    config, monkeypatch, failure,
):
    acquired = observe_admission(monkeypatch)
    calls = []

    async def run(selected):
        assert selected is config
        assert len(acquired) == 1
        assert record(acquired[0])["state"] == "running"
        assert record(acquired[0])["service"] == "agent-ssh-service"
        calls.append("work")
        if failure:
            raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(hosting, "run", run)
    if failure:
        with pytest.raises(RuntimeError, match="synthetic startup failure"):
            await _foreground(config)
    else:
        assert await _foreground(config) == 0
    assert calls == ["work"]
    assert acquired[0]._closed
    assert record(acquired[0])["state"] == "finished"


async def test_foreground_admission_refusal_precedes_service_or_native_work(config, monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(hosting, "run", run)
    monkeypatch.setattr(
        instances, "admit_daemon_instance", Mock(side_effect=ValueError("admission refused")),
    )
    with pytest.raises(ValueError, match="admission refused"):
        await _foreground(config)
    run.assert_not_awaited()


async def test_foreground_actual_writer_and_store_remain_inside_admission(config, monkeypatch):
    from pathlib import Path
    from single_instance_lease import AlreadyRunningError, SingleInstance

    acquired = observe_admission(monkeypatch)
    root = Path(config.to_dict()["data_dir"])

    class Connector:
        def __init__(self, **_):
            pass

        async def run(self, stop):
            assert record(acquired[0])["state"] == "running"
            assert (root / "directory.sqlite3").is_file()
            contender = SingleInstance(str(root), service="agent-ssh-service")
            with pytest.raises(AlreadyRunningError):
                contender.acquire()
            stop.set()

    monkeypatch.setattr(hosting, "ServiceConnector", Connector)
    monkeypatch.setattr(
        hosting, "load_service_credentials",
        lambda **_: SimpleNamespace(access_token=None, proof_key=None),
    )
    assert await _foreground(config) == 0
    assert record(acquired[0])["state"] == "finished"
    contender = SingleInstance(str(root), service="agent-ssh-service")
    contender.acquire()
    contender.release()


async def test_foreground_retains_same_admission_and_writer_until_original_relay_settles(
    config, monkeypatch,
):
    acquired = observe_admission(monkeypatch)
    calls = []
    lease = SimpleNamespace(release=lambda: calls.append("writer-released"))
    relay = SimpleNamespace(process=SimpleNamespace(returncode=None))

    class Pending(hosting.UnsettledRelay):
        async def settle(self):
            assert self.relay is relay and self.lease is lease
            assert len(acquired) == 1
            assert record(acquired[0])["state"] == "running"
            calls.append("settling")
            await asyncio.sleep(0)
            assert record(acquired[0])["state"] == "running"
            self.relay.process.returncode = 0
            self.lease.release()
            calls.append("settled")

    async def run(_selected):
        calls.append("run")
        raise Pending(relay, lease, {})

    monkeypatch.setattr(hosting, "run", run)
    assert await _foreground(config) == 1
    assert calls == ["run", "settling", "writer-released", "settled"]
    assert relay.process.returncode == 0
    assert len(acquired) == 1 and acquired[0]._closed
    assert record(acquired[0])["state"] == "finished"


async def test_foreground_borrows_existing_owner_without_releasing_it(config, monkeypatch):
    outer = instances.admit_daemon_instance(
        "agent-ssh-service", source=__file__, purpose="production",
        runtime_root=config.to_dict()["data_dir"],
    )
    before = {row["instance_id"] for row in instances.inspect_instances(outer.receipt.registry_root)}
    acquired = observe_admission(monkeypatch)
    monkeypatch.setattr(hosting, "run", AsyncMock())
    try:
        assert await _foreground(config) == 0
        assert acquired[0].receipt == outer.receipt
        assert {row["instance_id"] for row in instances.inspect_instances(
            outer.receipt.registry_root,
        )} == before
        assert record(outer)["state"] == "running"
    finally:
        outer.close()
    assert record(outer)["state"] == "finished"


async def test_foreground_accepts_parent_components_in_valid_absolute_data_dir(config, monkeypatch):
    from pathlib import Path

    from agent_ssh_service.config import ServiceConfig

    selected = config.to_dict()
    root = Path(selected["data_dir"])
    (root / "child").mkdir(parents=True)
    selected["data_dir"] = str(root / "child" / "..")
    selected = ServiceConfig.from_dict(selected)
    run = AsyncMock()
    monkeypatch.setattr(hosting, "run", run)
    assert await _foreground(selected) == 0
    run.assert_awaited_once_with(selected)
