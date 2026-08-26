from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-version-consistency.py"
_spec = importlib.util.spec_from_file_location("check_version_consistency", SCRIPT)
checker = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "_build_info.py"
    path.write_text(text, encoding="utf-8")
    return path


def test_build_info_version_accepts_constant_string_quotes(tmp_path: Path) -> None:
    assert checker._build_info_version(
        _write(tmp_path, "__version__ = '1.2.3'\n")
    ) == (True, "1.2.3")


def test_build_info_version_fails_closed_when_declared_but_invalid(
    tmp_path: Path,
) -> None:
    assert checker._build_info_version(
        _write(tmp_path, '"""__version__ is stamped below."""\n')
    ) == (True, None)
    assert checker._build_info_version(
        _write(tmp_path, "__version__ = compute_version()\n")
    ) == (True, None)


def test_build_info_without_source_version_is_not_opted_in(tmp_path: Path) -> None:
    assert checker._build_info_version(
        _write(tmp_path, 'BUILD_INFO = {"version": ""}\n')
    ) == (False, None)
