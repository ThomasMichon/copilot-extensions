"""Regression tests for the marker-only runtime-resolution guard.

Focus: the guard must not false-positive on a `.venv`/PATH-python path that only
appears inside a PowerShell ``<# .. #>`` block comment (the line-based `#` skip
alone misses those). Also pins the real-violation and allow-marker behaviour.

Run:  python -m pytest tools/test_check_runtime_resolution.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-runtime-resolution.py"

_spec = importlib.util.spec_from_file_location("check_runtime_resolution", SCRIPT)
crr = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(crr)


def _kinds(path: Path) -> list[str]:
    return [kind for _n, kind, _s in crr._violations(path)]


def test_strip_ps_block_comments_inline_and_spanning() -> None:
    # Inline <# .. #> removed; code on either side preserved.
    code, in_block = crr._strip_ps_block_comments("a <# junk #> b", False)
    assert code == "a  b" and in_block is False
    # Opener with no closer -> rest is comment, state stays open.
    code, in_block = crr._strip_ps_block_comments("keep <# open", False)
    assert code == "keep " and in_block is True
    # While open, everything is comment until the closer.
    code, in_block = crr._strip_ps_block_comments("still comment #> tail", True)
    assert code == " tail" and in_block is False


def test_block_comment_venv_path_is_not_flagged(tmp_path: Path) -> None:
    p = tmp_path / "scripts" / "install.ps1"
    p.parent.mkdir(parents=True)
    p.write_text(
        "<#\n"
        "   A legacy real `.venv\\Scripts\\python.exe` must be released first.\n"
        "#>\n"
        "$slot = Join-Path $root 'versions\\1.0\\Scripts\\python.exe'\n",
        encoding="utf-8",
    )
    assert _kinds(p) == []


def test_real_venv_link_launch_is_flagged(tmp_path: Path) -> None:
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text('_py="$_root/.venv/bin/python"\n', encoding="utf-8")
    assert _kinds(p) == ["venv-link"]


def test_allow_marker_suppresses(tmp_path: Path) -> None:
    p = tmp_path / "scripts" / "verify.sh"
    p.parent.mkdir(parents=True)
    p.write_text(
        'exec python3 -m x "$@"  # runtime-resolution: allow bootstrap\n',
        encoding="utf-8",
    )
    assert _kinds(p) == []
