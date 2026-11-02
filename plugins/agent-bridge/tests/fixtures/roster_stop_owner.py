"""Bounded parent watchdog drives this actual hosting/native-work probe."""

import asyncio
from concurrent.futures import Future
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from agent_bridge.app_agent_roster import stop_agent_roster_cache
from agent_bridge.agent_registry_cache import AgentRosterCache
from agent_bridge.agent_registry_discovery import DiscoveryResult
from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.app_agent_roster import _RosterHosting
from agent_procutil.daemon_instances import admit_daemon_instance, inspect_instances

registry = Path(sys.argv[2])


def report(marker, **fields):
    print(json.dumps({
        "marker": marker, "state": inspect_instances(registry)[0]["state"], **fields,
    }), flush=True)


async def main():
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    native = []
    resolver = AgentResolver({}, {})

    def scan(*_args, **_kwargs):
        native.append(threading.current_thread())
        entered.set()
        try:
            assert release.wait(30)
            return DiscoveryResult(ok=True)
        finally:
            returned.set()

    resolver._scan_provider_report = scan
    cache = AgentRosterCache(resolver, refresh_interval=60)
    custody = None if sys.argv[1] == "late-lost" else _RosterHosting(cache)
    cache.start()
    assert await asyncio.to_thread(entered.wait, 5)
    if custody is None:
        await cache.stop()  # The original cancellation has already erased its native receipt.
        app = SimpleNamespace(state=SimpleNamespace(agent_roster_cache=cache))
        stopping = asyncio.create_task(stop_agent_roster_cache(app))
        await asyncio.sleep(.1)
        custody = cache._hosting_custody
        report("responsive", pending=custody.pending, native_held=native[0].is_alive())
        release.set()
        while not returned.is_set() or native[0].is_alive():
            await asyncio.sleep(.01)
        report("unknown-hold", pending=custody.pending, native_retired=True)
        await stopping
        return
    sample = Future()
    sample.set_running_or_notify_cancel()
    cache._provider_work.samples.add(sample)
    stopping = asyncio.create_task(custody.stop(settle=True))
    while custody.stopping is None:
        await asyncio.sleep(.001)
    await asyncio.sleep(.02)
    assert not custody.stopping.done()
    if sys.argv[1] == "all":
        for task in tuple(asyncio.all_tasks()):
            if task is not asyncio.current_task():
                task.cancel()
    else:
        custody.stopping.cancel()
    await asyncio.sleep(.1)
    report("responsive", pending=custody.pending, native_held=native[0].is_alive())
    release.set()
    sample.set_result(None)
    while not returned.is_set() or native[0].is_alive():
        await asyncio.sleep(.01)
    if sys.argv[1] == "all":
        await asyncio.sleep(.1)
        report("unknown-hold", pending=custody.pending, native_retired=True)
    try:
        await stopping
    except asyncio.CancelledError:
        pass


with admit_daemon_instance(
    "bridge-stop-probe", runtime_root=Path.cwd(), source=__file__, purpose="production",
):
    asyncio.run(main())
report("settled")
