"""Real Windows cutovers retain the server after caller and Job cleanup."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

import agent_procutil as pu
import pytest

from . import windows_cutover_identity_fixture as child
from . import windows_durable_launch_fixture as fixture

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows trampoline and Job lifetime"),
    pytest.mark.timeout(180),
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.effect("network"),
    pytest.mark.contract("agent_dispatch.windows_cutover_identity"),
]


@pytest.mark.parametrize("contained", [True, False], ids=["contained", "production-lifetime"])
def test_windows_cutover_identity(tmp_path, contained, record_property):
    root = tmp_path / "isolated runtime"
    root.mkdir()
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("AGENT_DISPATCH_"):
            del env[name]
    env.update({
        "AGENT_DISPATCH_INSTALL_DIR": str(root),
        "AGENT_DISPATCH_DB": str(root / "tasks.db"),
        "AGENT_DISPATCH_ROUTING_DIR": str(root / "routing"),
        "AGENT_DISPATCH_RUN_DIR": str(root / "run"),
        "AGENT_DISPATCH_HOST": "127.0.0.1",
        "AGENT_DISPATCH_GC_INTERVAL": "0",
        "AGENT_DISPATCH_NO_AUTOSTART": "1",
        "PYTHONPATH": os.pathsep.join(sys.path),
    })
    # The supervisor remains contained. Only finite, private, pinned fixture
    # descendants test the production breakaway seam, matching the existing
    # windows_durable_launch_lifetime policy isolation. No installed service.
    if not contained:
        env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
        env.pop("PYTEST_CURRENT_TEST", None)
    else:
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    api = fixture.kernel()
    handles = []
    caller = job = None
    try:
        with (root / "caller.log").open("wb") as log:
            caller, job = pu.spawn_sync_in_kill_on_close_job(
                [sys.executable, str(Path(child.__file__).resolve()), "caller", str(root)],
                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                **pu.no_window_kwargs(),
            )
            assert job is not None
            deadline = time.monotonic() + 130
            while not (root / "ready.json").exists():
                assert caller.poll() is None, (root / "caller.log").read_text(errors="replace")
                assert time.monotonic() < deadline, (root / "caller.log").read_text(errors="replace")
                time.sleep(.1)
            ready = json.loads((root / "ready.json").read_text())
            pair = ready["pair"]
            assert len(pair) == 2, "must exercise a real venv launcher/server pair"
            assert str(ready["active"]["pid"]) in pair
            for pid, token in pair.items():
                handle = api.OpenProcess(0x00100000 | 0x1000 | 1, False, int(pid))
                assert handle
                handles.append(handle)
                assert str(fixture.birth(api, handle)) == token
                member = wintypes.BOOL()
                assert api.IsProcessInJob(handle, job.handle, ctypes.byref(member))
                assert bool(member.value) is contained
            (root / "pinned").touch()
            assert caller.wait(timeout=10) == 0
            job.close()
            if contained:
                for handle in handles:
                    assert api.WaitForSingleObject(handle, 5000) == 0
            else:
                port = ready["active"]["port"]
                for _ in range(3):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                        health = json.load(response)
                    assert health["slot"]["pid"] == ready["active"]["pid"]
                    assert health["slot"]["role"] == "active"
                    for handle in handles:
                        assert api.WaitForSingleObject(handle, 0) == 258
                    time.sleep(1)
            record_property("two_cutovers_seconds", round(ready["elapsed"], 2))
            print(f"two real cutovers: {ready['elapsed']:.2f}s; contained={contained}; "
                  "caller exited and Job closed; server identity/ownership verified")
    finally:
        for handle in reversed(handles):
            if api.WaitForSingleObject(handle, 0) == 258:
                api.TerminateProcess(handle, 1)
                api.WaitForSingleObject(handle, 5000)
            api.CloseHandle(handle)
        if job is not None:
            job.close()
        if caller is not None:
            if caller.poll() is None:
                caller.kill()
            caller.wait(timeout=5)
        # Failed readiness may precede the handoff. Reap only fixture-recorded
        # creation identities, never a name-based host sweep.
        from zdd.diagnostics import terminate_pid_if_identity

        for path in root.glob("owned-*.json"):
            owned = json.loads(path.read_text())
            terminate_pid_if_identity(owned["pid"], owned["token"])
