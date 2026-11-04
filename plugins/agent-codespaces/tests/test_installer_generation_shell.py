"""Nounset-safe installer argv contracts, including stock macOS Bash 3.2."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil

import pytest

from agent_procutil.owned_process import run_caller_owned

from .test_installer_engine_adoption import _env, _function

pytestmark = [pytest.mark.timeout(60), pytest.mark.installer_only]


@pytest.fixture
def bash():
    selected = os.environ.get("CODESPACES_TEST_BASH")
    if selected:
        assert Path(selected).is_file(), "explicit Bash fixture is unavailable"
        return selected
    if os.name == "nt":
        git = shutil.which("git")
        selected = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        if selected and selected.is_file():
            return str(selected)
        pytest.skip("Git Bash unavailable")
    selected = shutil.which("bash")
    if selected is None:
        pytest.skip("Bash unavailable")
    return selected


def _shell(bash, tmp_path, body):
    script = tmp_path / "harness.sh"
    script.write_text("set -eu\n" + body, encoding="utf-8", newline="\n")
    env = _env(tmp_path)
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    result = run_caller_owned(
        [bash, script.name], cwd=tmp_path, env=env, timeout=45,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("receipt", ["", '{"runtime_source":{"source_tree":"with spaces"}}'])
def test_generation_selection_optional_receipt_under_bash_nounset(tmp_path, bash, receipt):
    _shell(bash, tmp_path, _function("sh", "_select_install_runtime_generation") + f"""
SCRIPT_DIR='payload scripts'
INSTALL_DIR='runtime home'
SOURCE_VERSION=1.2.3
SOURCE_RUNTIME_IDENTITY={shlex.quote(receipt)}
_assert_source_runtime() {{ :; }}
_bootstrap_python() {{ printf '%s\\n' fixture_python; }}
_payload_hash() {{ printf '%s\\n' '{'a' * 64}'; }}
fixture_python() {{
    printf '%s\\0' "$@" > selection-args
    printf '%s\\n' 1.2.3
}}
_select_install_runtime_generation
[[ "$VENV_DIR" == 'runtime home/versions/1.2.3' ]]
""")
    arguments = (tmp_path / "selection-args").read_bytes()[:-1].decode().split("\0")
    expected = [
        "-I", "-B", "payload scripts/runtime-generation.py", "--root", "runtime home",
        "--version", "1.2.3", "--payload-hash", "a" * 64,
    ]
    if receipt:
        expected += ["--source-identity", receipt]
    assert arguments == expected


def test_activation_gc_without_keep_arguments_under_bash_nounset(tmp_path, bash):
    python = tmp_path / "slot" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/bash\nset -eu\ncase "$*" in\n'
        '  *" current") :;;\n'
        '  *" gc "*) printf \'%s\\0\' "$@" > gc-args;;\n'
        'esac\n',
        encoding="utf-8", newline="\n",
    )
    python.chmod(0o755)
    _shell(bash, tmp_path, _function("sh", "_versioned_activate") + """
SCRIPT_DIR='payload scripts'
INSTALL_DIR='runtime home'
VENV_DIR=slot
VERSIONED_RUNTIME=1
SRC_VERSION=1.2.3
SOURCE_VERSION=1.2.3
SOURCE_RUNTIME_IDENTITY=''
_assert_source_runtime() { :; }
_codespaces_runtime_healthy() { :; }
_versioned_mark_complete() { :; }
_ok() { :; }
_versioned_activate
""")
    arguments = (tmp_path / "gc-args").read_bytes()[:-1].decode().split("\0")
    assert arguments == [
        "payload scripts/versioned_runtime.py", "--root", "runtime home",
        "--link-name", ".venv", "gc", "--protect-pids",
    ]
