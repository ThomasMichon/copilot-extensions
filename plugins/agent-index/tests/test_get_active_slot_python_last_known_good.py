"""Regression guard: `Get-ActiveSlotPython` must prefer last-known-good over
a raw newest-slot guess when the current-version marker is missing/stale
(#742) -- matching the canonical resolve-runtime.ps1 chain, so a call during
a brief mid-swap window can't bind a still-installing or never-activated slot.
"""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"


def _text() -> str:
    return _INSTALL_PS1.read_text(encoding="utf-8")


def test_get_active_slot_python_tries_last_known_good_before_newest_slot_guess():
    text = _text()
    start = text.index("function Get-ActiveSlotPython")
    end = text.index("\nfunction ", start + 1)
    body = text[start:end]
    marker_idx = body.index("current-version")
    lkg_idx = body.index("last-known-good")
    guess_idx = body.index("Sort-Object Name")
    assert marker_idx < lkg_idx < guess_idx, (
        "last-known-good must be consulted after the current-version marker "
        "but strictly before the newest-slot guess"
    )
