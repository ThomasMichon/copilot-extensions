"""Generation management converges through real native endpoint and writer custody."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING

import pytest

from agent_vault import launch, service, service_generation as generation
from agent_vault.rendezvous import read_endpoint


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native process identity and local transport",
)
def test_management_ensure_converges_on_a_real_ready_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    if TYPE_CHECKING:
        from test_service_generation import OwnedChild, _endpoint, _required_endpoint, _settle, _start, _until
    else:
        from .test_service_generation import OwnedChild, _endpoint, _required_endpoint, _settle, _start, _until

    marker = tmp_path / "current-version"
    marker.write_text("newer", encoding="utf-8")
    durable = tmp_path / "protected-state"
    durable.write_bytes(b"fixture protected state")
    old = _start(tmp_path, "newer", 0)
    children: list[OwnedChild] = [old]
    launches: list[list[str]] = []
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        initial = _required_endpoint(tmp_path)
        old_pid = initial["pid"]
        monkeypatch.setenv("AGENT_VAULT_HOME", str(tmp_path))
        monkeypatch.delenv("AGENT_VAULT_INSTALLATION_ID", raising=False)
        monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
        monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
        monkeypatch.setattr(generation, "PROCESS_SLOT", tmp_path / "versions" / "older")
        monkeypatch.setattr(generation, "PROCESS_GENERATION", "older")
        marker.write_text("older", encoding="utf-8")

        def spawn(arguments: list[str]) -> None:
            launches.append(list(arguments))
            children.append(_start(tmp_path, "older", 0))

        monkeypatch.setattr(launch, "spawn_service", spawn)
        cold_started = time.perf_counter()
        assert generation.ensure_selected(timeout=15)
        cold_seconds = time.perf_counter() - cold_started
        endpoint = read_endpoint(tmp_path / "run")
        ready = generation.endpoint_health(endpoint)
        assert ready is not None and ready["pid"] != old_pid
        assert generation.same_generation(ready) and ready["accepting_work"] is True
        warm_started = time.perf_counter()
        assert generation.ensure_selected(timeout=1)
        warm_seconds = time.perf_counter() - warm_started
        record_testsuite_property("generation_management_cold_seconds", f"{cold_seconds:.6f}")
        record_testsuite_property("generation_management_warm_seconds", f"{warm_seconds:.6f}")
        assert launches == [["--foreground", "--persistent"]]
        assert durable.read_bytes() == b"fixture protected state"
    finally:
        _settle(tmp_path, children)
