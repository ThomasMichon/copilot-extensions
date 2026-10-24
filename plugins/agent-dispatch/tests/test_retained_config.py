"""Dispatch's actual configuration and lifespan consumers of shared retention."""

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from process_telemetry import SinkSlot

from agent_dispatch import telemetry


@pytest.fixture
def configured(tmp_path, monkeypatch):
    slot = SinkSlot()
    monkeypatch.setattr(telemetry, "_slot", slot)
    directory = tmp_path / "bucket"
    (directory / "retained").mkdir(parents=True)
    path = tmp_path / "telemetry.json"
    path.write_text(json.dumps({"retained_spool": {"directory": str(directory)}}))
    monkeypatch.setattr(telemetry, "_default_config_path", lambda: path)
    yield path, directory, slot
    slot.set(None)
    if slot._last_buffer is not None:
        assert slot._last_buffer.join(2)


def test_actual_config_loader_and_real_lifespan_cleanup(configured, tmp_path):
    from agent_dispatch.coordinator import create_app
    from agent_dispatch.queue import TaskQueue

    path, directory, slot = configured
    assert telemetry.load_sink_from_config(path)
    writer = slot.sink
    event = {"ts": int(time.time() * 1000), "kind": "state", "name": "λ🚀"}
    end = time.monotonic() + 5
    before = writer.stats()["accepted"]
    while writer.stats()["accepted"] == before:
        telemetry.emit(event)
        assert time.monotonic() < end
        if writer.stats()["accepted"] == before:
            time.sleep(0.01)
    while writer.stats()["delivered"] < 1:
        assert time.monotonic() < end
        time.sleep(0.01)
    active = directory / "retained" / "agent-dispatch-telemetry.jsonl"
    assert json.loads(active.read_text(encoding="utf-8")) == event
    path.write_text(json.dumps({"retained_spool": {}, "sink": "private:factory"}))
    assert not telemetry.load_sink_from_config(path)
    assert slot.sink is writer
    app = create_app(
        TaskQueue(tmp_path / "tasks.db"), sweep_interval=0, wake_interval=0,
        verification_interval=0, enable_mcp=False,
    )
    async def run():
        async with app.router.lifespan_context(app):
            pass
    asyncio.run(run())
    assert writer.closed and not telemetry.has_sink()


def test_real_cli_reports_configured_namespace_without_writing(configured, tmp_path):
    _, directory, _ = configured
    environment = dict(os.environ, AGENT_DISPATCH_CONFIG_DIR=str(tmp_path),
                       AGENT_DISPATCH_INSTALL_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "agent_dispatch", "logs"], env=environment,
        capture_output=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0 and result.stderr == b""
    report = json.loads(result.stdout)["telemetry"]
    assert report["active"] == str(directory / "retained" / "agent-dispatch-telemetry.jsonl")
    assert report["write_health"] == "unknown-from-discovery"
    assert list((directory / "retained").iterdir()) == []
