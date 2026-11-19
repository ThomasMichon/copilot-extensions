"""Versioned routing is scoped without relaxing legacy execution context."""

import os
from pathlib import Path

import pytest

from agent_worktrees import update_driver_cli as boundary


def test_engine_inventory_advertises_its_context_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import installation_updates, project_update_controller, reconcile

    monkeypatch.setattr(installation_updates.update_runtime, "_registered_plugin_targets", lambda: {})
    monkeypatch.setattr(reconcile, "discover_core_payload_dirs", lambda: [])
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _name: None)
    monkeypatch.setattr(project_update_controller, "delegation_capability", lambda: None)
    assert installation_updates.inventory()["toolchain_request_context"] == {
        "schema": "copilot-extensions.toolchain-request-context", "version": 1,
    }


def test_engine_retains_versioned_toolchain_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    for key in boundary._SUPPORTED_CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST", "1")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG", str(tmp_path / "toolchain.conf"))
    monkeypatch.setenv("PIP_INDEX_URL", "https://caller.example.invalid/simple/")
    context = dict(boundary._request_context(cwd=tmp_path))
    assert context["PIP_INDEX_URL"] == "https://caller.example.invalid/simple/"
    assert context["COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST"] == "1"
    assert "UV_CONFIG_FILE" not in context
    assert "UV_CONFIG_FILE" in boundary._execution_context_keys(context)
    monkeypatch.setenv("UV_CONFIG_FILE", "foreign-drainer-selection")
    with boundary._execution_context(context):
        assert "UV_CONFIG_FILE" not in os.environ
        assert os.environ["PIP_INDEX_URL"] == context["PIP_INDEX_URL"]
    assert os.environ["UV_CONFIG_FILE"] == "foreign-drainer-selection"


@pytest.mark.parametrize("context", [
    {"COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST": "2", "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG": "selected"},
    {"COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST": "1"},
    {"PIP_INDEX_URL": "https://caller.example.invalid/simple/"},
    {"UNKNOWN": "unsupported"},
])
def test_engine_refuses_unknown_or_incomplete_toolchain_contract(
    context: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIP_INDEX_URL", "https://original.example.invalid/simple/")
    with pytest.raises(ValueError):
        with boundary._execution_context(context):
            pytest.fail("invalid context entered update execution")
    assert os.environ["PIP_INDEX_URL"] == "https://original.example.invalid/simple/"
