"""Run the original remote-shell callable cases without duplicating them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/remote-login-shell"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("remote_login_shell", LIBRARY / "src/remote_login_shell/__init__.py")
_original = _load_source(
    "promotion_original_remote_login_shell_tests",
    LIBRARY / "tests/test_remote_login_shell.py",
)

test_wrap_login_shell_defaults_to_bash = _original.test_wrap_login_shell_defaults_to_bash
test_wrap_login_shell_uses_configured_shell = (
    _original.test_wrap_login_shell_uses_configured_shell
)
test_wrap_login_shell_quotes_special_characters = (
    _original.test_wrap_login_shell_quotes_special_characters
)
test_is_posix_login_shell_true_for_documented_shells = (
    _original.test_is_posix_login_shell_true_for_documented_shells
)
test_is_posix_login_shell_false_for_everything_else = (
    _original.test_is_posix_login_shell_false_for_everything_else
)
