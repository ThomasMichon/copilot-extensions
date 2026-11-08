from __future__ import annotations

import os
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from agent_procutil.daemon_instances import daemon_test_lifecycle

from agent_index import serving_observation


def test_observes_a_real_local_host_without_starting_another(tmp_path, monkeypatch):
    root = tmp_path / "index"
    root.mkdir()
    registry = tmp_path / "registry"
    with daemon_test_lifecycle(owner="index-serving-observation", registry_root=registry) as scope:
        env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_INDEX_")}
        for key in tuple(os.environ):
            if key.startswith("AGENT_INDEX_"):
                monkeypatch.delenv(key, raising=False)
        env.update(scope)
        env.update({
            "AGENT_INDEX_HOME": str(root), "AGENT_INDEX_ROUTING_DIR": str(root),
            "AGENT_INDEX_RUN_DIR": str(root / "run"), "AGENT_INDEX_DATA_DIR": str(root / "data"),
            "AGENT_INDEX_ROLE": "host", "AGENT_INDEX_ENGINE_MODE": "external",
            "AGENT_INDEX_SEARCH_IN_PROCESS": "0", "AGENT_INDEX_LOG_LEVEL": "warning",
        })
        for key in ("AGENT_INDEX_HOME", "AGENT_INDEX_ROUTING_DIR", "AGENT_INDEX_RUN_DIR"):
            monkeypatch.setenv(key, env[key])
        log_path = tmp_path / "service.log"
        with log_path.open("wb") as log:
            process, job = spawn_sync_in_kill_on_close_job(
                [sys.executable, "-I", "-B", "-m", "agent_index", "start", "--port", "0"],
                cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                require_job=True, allow_breakaway=False, **no_window_kwargs(),
            )
            try:
                deadline = time.monotonic() + 30
                result = {"state": "unknown"}
                while time.monotonic() < deadline and process.poll() is None:
                    result = serving_observation.observe_local_serving(root)
                    if result["state"] == "ready":
                        break
                    time.sleep(0.1)
                assert result["state"] == "ready", log_path.read_text(errors="replace")
                if os.name == "nt":
                    from agent_procutil._windows_caller_owned import _job_pids

                    assert job is not None and result["pid"] in _job_pids(job.handle)
                else:
                    assert result["pid"] == process.pid
                assert serving_observation.observe_local_serving(root)["pid"] == result["pid"]
            finally:
                if job is not None:
                    job.terminate_and_wait(10)
                elif process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
