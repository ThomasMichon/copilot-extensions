from __future__ import annotations

import sys
from pathlib import Path
import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB / "src"))


@pytest.fixture(autouse=True)
def isolated_instance_registry(tmp_path, monkeypatch, request):
    """Library fixtures never create records in the host's production registry."""
    monkeypatch.setenv("COPILOT_EXTENSIONS_DAEMON_REGISTRY", str(tmp_path / "instances"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_INSTANCE", raising=False)
    native_launch_modules = {
        "test_native_daemon.py", "test_native_daemon_cleanup.py",
        "test_spawn_daemon_windows_integration.py",
    }
    if Path(request.node.path).name not in native_launch_modules:
        monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", raising=False)


@pytest.fixture
def declared_mock_daemon(tmp_path, monkeypatch, request):
    """Fake executables declare fixture provenance rather than exercising lookup."""
    import agent_procutil as pu
    from agent_procutil import _daemon_instance_store as store
    from agent_procutil.daemon_instances import DaemonInstanceSpec

    original = pu.spawn_daemon

    def launch(*args, **kwargs):
        if "instance" not in kwargs:
            kwargs["instance"] = DaemonInstanceSpec(
                "mock-daemon", tmp_path, Path(request.node.path).resolve(),
            )
        return original(*args, **kwargs)
    monkeypatch.setattr(pu, "spawn_daemon", launch)
    monkeypatch.setattr(store, "bind_process", lambda *_a, **_kw: None)
