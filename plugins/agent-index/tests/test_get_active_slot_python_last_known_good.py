"""Management selection shares the binstub's completed-generation resolver."""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"


def _text() -> str:
    return _INSTALL_PS1.read_text(encoding="utf-8")


def test_get_active_slot_python_uses_the_canonical_read_only_resolver() -> None:
    text = _text()
    start = text.index("function Get-ActiveSlotPython")
    end = text.index("\nfunction ", start + 1)
    body = text[start:end]
    assert body.index("'resolve-runtime.ps1'") < body.index("Get-IndexManagementPython")
    assert "-TraceMode read-only" in body
    assert "$env:AGENT_RT_ROOT = $previousRoot" in body
    assert "$LinkPython" not in body
    assert "Sort-Object" not in body
