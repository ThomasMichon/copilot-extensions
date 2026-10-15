"""Contained real-CLI proof of non-activation and a current daemon's no-op."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
from zdd import diagnostics, process_exit, routing

from agent_bridge.client import BridgeClient
from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV


def _environment(root: Path) -> dict[str, str]:
    return {
        **os.environ, CONFIG_DIR_ENV: str(root), INSTALL_DIR_ENV: str(root),
        "AGENT_BRIDGE_WATCHDOG": "0",
    }


def _run_reconcile(root: Path, *, timeout: float = 12) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agent_bridge", "deploy", "--reconcile-serving", "--json"],
        cwd=root, env=_environment(root), capture_output=True, text=True,
        timeout=timeout, check=False, **no_window_kwargs(),
    )


def _stop_fixture(daemon, job) -> None:
    if job is not None:
        job.close()
    if daemon.poll() is None:
        daemon.terminate()
    daemon.wait(timeout=2)


@pytest.mark.parametrize(("body", "code", "state"), [
    (None, 0, "inactive-or-forwarded"),
    ('{"active":{"port":9011,"forwarded":true}}', 0, "inactive-or-forwarded"),
    ('{"active":{"port":9011}}', 0, "inactive-or-forwarded"),
    ("{broken", 1, None),
])
def test_serving_reconcile_native_first_touch_never_activates(tmp_path, body, code, state):
    root = tmp_path / "runtime"
    root.mkdir()
    if body is not None:
        (root / "active.json").write_text(body)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = _run_reconcile(root)
    assert result.returncode == code, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is (code == 0)
    if state is not None:
        assert data["serving_reconcile"] == state
        assert data["skipped"] is True
    else:
        assert "error" in data
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="native reference platform")
def test_serving_reconcile_native_current_daemon_keeps_same_process(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "config.yaml").write_text(
        "schema_version: 2\nport: 0\nbind: 127.0.0.1\n"
        "enable_credential_relay: false\ntopologies: {}\n"
        "retention:\n  enabled: false\n",
    )
    token = "contained-fixture-token"
    (root / "auth.yaml").write_text(f"token: {token}\n")
    # One shared budget includes cold daemon imports and the cold client, with
    # four seconds left for cleanup inside the runner's unchanged 30-second cap.
    deadline = time.monotonic() + 25
    with (root / "fixture.log").open("wb") as log, ExitStack() as cleanup:
        daemon, job = spawn_sync_in_kill_on_close_job(
            [sys.executable, "-u", "-m", "agent_bridge", "start"],
            require_job=sys.platform == "win32", allow_breakaway=False,
            cwd=root, env=_environment(root), stdout=log, stderr=log,
            **no_window_kwargs(),
        )
        cleanup.callback(_stop_fixture, daemon, job)
        while time.monotonic() < deadline - 8:
            table = routing.read_table(root) or {}
            row = table.get("active")
            if isinstance(row, dict):
                endpoint = routing.Endpoint.from_dict(row)
                if endpoint is not None:
                    try:
                        with urllib.request.urlopen(
                            f"{endpoint.base_url}/health", timeout=0.25,
                        ) as response:
                            if json.load(response).get("ready") is True:
                                break
                    except (OSError, ValueError):
                        pass
            assert daemon.poll() is None, (root / "fixture.log").read_text()
            time.sleep(0.05)
        else:
            pytest.fail(f"fixture daemon never became ready: {(root / 'fixture.log').read_text()}")
        reference = process_exit.open_process(
            row["pid"], diagnostics.process_start_time(row["pid"]),
        )
        cleanup.callback(reference.close)
        result = _run_reconcile(root, timeout=min(12, deadline - time.monotonic()))
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["serving_reconcile"] == "current"
        assert (routing.read_table(root) or {}).get("active") == row
        assert reference.wait(0) is False
        BridgeClient(endpoint.base_url, token, timeout=1).shutdown()
        assert reference.wait(2), "owned fixture daemon did not gracefully stop"
