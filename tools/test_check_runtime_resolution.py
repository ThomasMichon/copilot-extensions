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


def test_excluded_helper_file_by_path_is_skipped(tmp_path: Path) -> None:
    # A dev-only helper (preview-picker) prints setup instructions that mention a
    # `.venv` path; the whole file is skipped by path, not flagged line by line.
    p = tmp_path / "scripts" / "preview-picker.sh"
    p.parent.mkdir(parents=True)
    p.write_text(
        'echo "uv pip install --python .venv/bin/python -e .[dev]" >&2\n',
        encoding="utf-8",
    )
    assert _kinds(p) == []


def test_console_script_through_venv_link_is_flagged(tmp_path: Path) -> None:
    # The shape that actually shipped: `activate --no-link` leaves no link, so a
    # console script resolved through one is unreachable and the caller fails
    # closed. The `$LINK_DIR` spelling is the one used by the installers.
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text(
        'nohup "$LINK_DIR/bin/agent-bridge" start > "$LOG" 2>&1 &\n',
        encoding="utf-8",
    )
    assert _kinds(p) == ["venv-link-script"]


def test_console_script_through_literal_venv_dir_is_flagged(tmp_path: Path) -> None:
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text('"$_root/.venv/bin/session-sync" status || true\n', encoding="utf-8")
    assert _kinds(p) == ["venv-link-script"]


def test_slot_console_script_is_not_flagged(tmp_path: Path) -> None:
    # A versioned slot path is the correct resolution and must stay clean.
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text(
        '"$INSTALL_DIR/versions/$SRC_VERSION/bin/agent-bridge" version\n',
        encoding="utf-8",
    )
    assert _kinds(p) == []


def test_link_dir_python_stays_venv_link_not_script(tmp_path: Path) -> None:
    # `$LINK_DIR/bin/python` is bootstrap discovery, deliberately excluded from
    # the console-script rule so the sibling installers are not mass-flagged.
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text('if [[ -x "$LINK_DIR/bin/python" ]]; then :; fi\n', encoding="utf-8")
    assert _kinds(p) == []


def test_link_dir_substring_is_not_flagged(tmp_path: Path) -> None:
    # `LINK_DIR` must be an expansion, not a substring of a longer identifier:
    # `$SOME_LINK_DIR/bin/tool` is a different variable and not a venv link.
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text('"$SOME_LINK_DIR/bin/tool" --version\n', encoding="utf-8")
    assert _kinds(p) == []


def test_braced_link_dir_expansion_is_flagged(tmp_path: Path) -> None:
    p = tmp_path / "scripts" / "install.sh"
    p.parent.mkdir(parents=True)
    p.write_text('"${LINK_DIR}/bin/session-sync" status\n', encoding="utf-8")
    assert _kinds(p) == ["venv-link-script"]
