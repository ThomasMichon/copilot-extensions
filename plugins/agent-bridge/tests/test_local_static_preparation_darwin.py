"""Native Darwin birth identity and owned-group descendant retirement."""

import asyncio
import contextlib
import json
import os
import sys
import time

import psutil
import pytest

from agent_bridge import local_cache_refresh as preparation

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
    pytest.mark.skipif(sys.platform != "darwin", reason="native macOS process-group qualification"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "owner-failure"])
async def test_native_darwin_cleanup_retires_owned_descendant(tmp_path, ending):
    ready, finish = tmp_path / "ready.json", tmp_path / "finish"
    script = (
        "import json,os,pathlib,psutil,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "members=[(pid,psutil.Process(pid).create_time()) for pid in (os.getpid(),child.pid)]\n"
        "path=pathlib.Path(sys.argv[1]);stage=path.with_suffix('.new')\n"
        "stage.write_text(json.dumps({'owner':os.getpid(),'group':os.getpgid(child.pid),'members':members}))\n"
        "os.replace(stage,path)\n"
        "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)\n"
        "os._exit(23)\n"
    )
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    task = asyncio.create_task(preparation._run_bounded_result(
        [sys.executable, "-c", script, str(ready), str(finish)],
        timeout=10, env=env, retire_group=True,
    ))
    members = []

    def live(pid, birth):
        try:
            process = psutil.Process(pid)
            return process.create_time() == birth and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    try:
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            await asyncio.sleep(.02)
        assert ready.exists(), "native owner did not reach descendant admission"
        data = json.loads(ready.read_text())
        members = data["members"]
        assert data["owner"] == data["group"] != os.getpgrp()
        assert preparation._process_start_time(data["owner"]) == f"darwin:{members[0][1]!r}"
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            finish.touch()
            assert (await task).returncode == 23
        until = time.monotonic() + 2
        while any(live(pid, birth) for pid, birth in members) and time.monotonic() < until:
            await asyncio.sleep(.02)
        assert not any(live(pid, birth) for pid, birth in members)
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if not members and ready.exists():
            members = json.loads(ready.read_text())["members"]
        for pid, birth in members:
            with contextlib.suppress(psutil.NoSuchProcess):
                process = psutil.Process(pid)
                if process.create_time() == birth:
                    process.kill()
                    process.wait(timeout=3)
