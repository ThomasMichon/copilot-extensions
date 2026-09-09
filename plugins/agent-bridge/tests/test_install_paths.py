from __future__ import annotations

from agent_bridge import config
from agent_bridge import elevated
from agent_bridge import provider_sources
from agent_bridge import runtime_version
from agent_bridge import telemetry
from agent_bridge.install_paths import (
    effective_config_dir,
    elevated_task_name,
    install_dir,
    installation_suffix,
    scheduled_task_name,
    systemd_unit_name,
)


def test_scoped_installations_derive_distinct_local_roots(monkeypatch, tmp_path):
    first = tmp_path / "install-a"
    second = tmp_path / "install-b"
    for name in (
        "AGENT_BRIDGE_INSTALL_DIR",
        "AGENT_BRIDGE_CONFIG_DIR",
        "AGENT_BRIDGE_PROVIDERS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(first))
    first_paths = {
        "install_dir": install_dir(),
        "config_dir": effective_config_dir(),
        "db": config.default_db_path(),
        "providers": provider_sources.providers_dir(),
        "runtime_version": runtime_version.install_dir(),
        "telemetry": telemetry._default_config_path(),
        "scheduled_task": scheduled_task_name(),
        "systemd_unit": systemd_unit_name(),
        "elevated_task": elevated_task_name(),
        "elevated_dir": elevated.elevated_dir(),
        "suffix": installation_suffix(),
    }

    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(second))
    second_paths = {
        "install_dir": install_dir(),
        "config_dir": effective_config_dir(),
        "db": config.default_db_path(),
        "providers": provider_sources.providers_dir(),
        "runtime_version": runtime_version.install_dir(),
        "telemetry": telemetry._default_config_path(),
        "scheduled_task": scheduled_task_name(),
        "systemd_unit": systemd_unit_name(),
        "elevated_task": elevated_task_name(),
        "elevated_dir": elevated.elevated_dir(),
        "suffix": installation_suffix(),
    }

    assert first_paths["suffix"]
    assert second_paths["suffix"]
    assert first_paths["suffix"] != second_paths["suffix"]
    assert first_paths["providers"] == first / "providers.d"
    assert second_paths["providers"] == second / "providers.d"
    assert first_paths["db"] == first / "sessions.db"
    assert second_paths["db"] == second / "sessions.db"
    assert {
        key for key in first_paths if first_paths[key] != second_paths[key]
    } == set(first_paths)
