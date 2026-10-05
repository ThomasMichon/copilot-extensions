from __future__ import annotations

import pytest

from remote_login_shell import POSIX_LOGIN_SHELLS, is_posix_login_shell, wrap_login_shell


def test_wrap_login_shell_defaults_to_bash():
    assert wrap_login_shell("echo hi") == "bash -lc 'echo hi'"


@pytest.mark.parametrize("shell", ["bash", "sh", "zsh"])
def test_wrap_login_shell_uses_configured_shell(shell):
    assert wrap_login_shell("echo hi", shell=shell) == f"{shell} -lc 'echo hi'"


def test_wrap_login_shell_quotes_special_characters():
    command = "echo 'hello world' && exit 1"
    wrapped = wrap_login_shell(command)
    assert wrapped == f"bash -lc {shlex_quote(command)}"


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def test_posix_login_shells_vocabulary():
    assert POSIX_LOGIN_SHELLS == ("bash", "sh", "zsh")


@pytest.mark.parametrize("shell", ["bash", "sh", "zsh"])
def test_is_posix_login_shell_true_for_documented_shells(shell):
    assert is_posix_login_shell(shell) is True


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "", "fish", "cmd"])
def test_is_posix_login_shell_false_for_everything_else(shell):
    assert is_posix_login_shell(shell) is False
