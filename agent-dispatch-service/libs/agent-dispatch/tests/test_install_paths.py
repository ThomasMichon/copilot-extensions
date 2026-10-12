from __future__ import annotations

from agent_dispatch import config
from agent_dispatch import procutil
from agent_dispatch import telemetry
from agent_dispatch.install_paths import installation_suffix
from agent_dispatch.managed_runtime import managed_runtime_root
from agent_dispatch.registrar_discovery import registrar_dir
from agent_dispatch.registrar_registry import registrar_dropins_dir


def test_scoped_installations_derive_distinct_local_roots(monkeypatch, tmp_path):
    first = tmp_path / "install-a"
    second = tmp_path / "install-b"
    for name in (
        "AGENT_DISPATCH_RUN_DIR",
        "AGENT_DISPATCH_ROUTING_DIR",
        "AGENT_DISPATCH_OVERRIDES",
        "AGENT_DISPATCH_REGISTRAR_DIR",
        "AGENT_DISPATCH_REGISTRAR_DROPINS_DIR",
        "AGENT_DISPATCH_MANAGED_RUNTIME_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(first))
    first_paths = {
        "install_suffix": installation_suffix(),
        "run_dir": config.run_dir(),
        "db": config.default_db_path(),
        "routing": config.routing_dir(),
        "overrides": config.overrides_path(),
        "registrar": registrar_dir(),
        "dropins": registrar_dropins_dir(),
        "runtime_root": procutil.runtime_root(),
        "telemetry": telemetry._default_config_path(),
        "managed_runtimes": managed_runtime_root(),
    }

    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(second))
    second_paths = {
        "install_suffix": installation_suffix(),
        "run_dir": config.run_dir(),
        "db": config.default_db_path(),
        "routing": config.routing_dir(),
        "overrides": config.overrides_path(),
        "registrar": registrar_dir(),
        "dropins": registrar_dropins_dir(),
        "runtime_root": procutil.runtime_root(),
        "telemetry": telemetry._default_config_path(),
        "managed_runtimes": managed_runtime_root(),
    }

    assert first_paths["install_suffix"]
    assert second_paths["install_suffix"]
    assert first_paths["install_suffix"] != second_paths["install_suffix"]
    assert first_paths["managed_runtimes"] == first / "mr"
    assert second_paths["managed_runtimes"] == second / "mr"
    assert {
        key for key in first_paths if first_paths[key] != second_paths[key]
    } == set(first_paths)
