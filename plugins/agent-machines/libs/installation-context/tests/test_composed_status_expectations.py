"""Composed status preserves every supplied expectation at real receipt admission."""

from __future__ import annotations

from argparse import Namespace
import importlib.util
from pathlib import Path
import sys

import pytest

from test_installation_context import _receipt_layout


@pytest.mark.parametrize("status", ["ready", "deactivation-required"])
@pytest.mark.parametrize("expectation", ["matching", "marketplace", "cell"])
def test_composed_status_checks_real_receipt_expectations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, expectation: str,
) -> None:
    source = Path(__file__).resolve().parents[1] / "installation_context.py"
    spec = importlib.util.spec_from_file_location("composed_status_sdk", source)
    assert spec is not None and spec.loader is not None
    sdk = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sdk
    spec.loader.exec_module(sdk)
    layout = _receipt_layout(tmp_path)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(sdk, "_is_wsl_guest", lambda: False)
    monkeypatch.setattr(sdk, "resolve_installation_mode", lambda **kwargs: {
        "actualMode": "namespaced", "status": status, "context": str(layout["install"]),
    })
    arguments = Namespace(
        with_context_validation=True,
        expected_marketplace_id=(
            "other--0123456789abcdef" if expectation == "marketplace" else layout["marketplace_id"]
        ),
        expected_plugin_id=layout["plugin_id"],
        expected_payload_root=str(layout["payload"]),
        expected_cell_root=str(tmp_path / "other-cell" if expectation == "cell" else layout["cell"]),
    )
    if expectation == "matching":
        result = sdk._resolve_cli_status(arguments, {})
        assert result["contextValidation"]["marketplaceId"] == layout["marketplace_id"]
        assert Path(result["contextValidation"]["cellRoot"]) == layout["cell"]
    else:
        message = "Expected marketplace" if expectation == "marketplace" else "Expected cell"
        with pytest.raises(sdk.InstallationContextError, match=message):
            sdk._resolve_cli_status(arguments, {})
