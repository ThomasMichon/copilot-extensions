"""Tests for the headless-launch guard (windows-launch-hardening #786).

The guard flags a raw process-creation flag literal in an ``agent-procutil``
adopter and forbids ``CREATE_NEW_CONSOLE`` across all production plugin and
canonical shared-library sources. It is AST-based (docstrings/comments never
count) and supports an inline ``# headless-guard: allow <reason>`` escape hatch.

Run:  python -m pytest tools/test_check_headless_launch.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "check-headless-launch.py"
_spec = importlib.util.spec_from_file_location("check_headless_launch", _SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _mk_plugin(root: Path, name: str, *, adopts: bool, body: str) -> None:
    p = root / "plugins" / name
    (p / "src" / name.replace("-", "_")).mkdir(parents=True)
    dep = '"agent-procutil",' if adopts else ""
    (p / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = [{dep}]\n', encoding="utf-8")
    (p / "src" / name.replace("-", "_") / "mod.py").write_text(body, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(guard, "LIBS_DIR", tmp_path / "libs")
    return tmp_path


def test_flags_raw_flag_in_adopting_plugin(repo):
    _mk_plugin(repo, "agent-foo", adopts=True,
               body="import subprocess\nx = subprocess.CREATE_NO_WINDOW\n")
    problems = guard.verify()
    assert any("agent-foo" in p and "CREATE_NO_WINDOW" in p for p in problems)


def test_ignores_non_adopting_plugin(repo):
    _mk_plugin(repo, "agent-bar", adopts=False,
               body="import subprocess\nx = subprocess.CREATE_NO_WINDOW\n")
    assert guard.verify() == []


def test_forbids_new_console_in_non_adopting_plugin(repo):
    _mk_plugin(repo, "agent-window", adopts=False,
               body="import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n")
    problems = guard.verify()
    assert any("unsafe 'CREATE_NEW_CONSOLE'" in p for p in problems)


def test_forbids_new_console_in_canonical_shared_library(repo):
    src = repo / "libs" / "ssh-manager" / "src" / "ssh_manager"
    src.mkdir(parents=True)
    (src / "process.py").write_text(
        "import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n",
        encoding="utf-8",
    )
    problems = guard.verify()
    assert any("libs/ssh-manager" in p and "CREATE_NEW_CONSOLE" in p
               for p in problems)


def test_forbids_aliased_new_console_import(repo):
    _mk_plugin(
        repo,
        "agent-alias",
        adopts=False,
        body=(
            "from subprocess import CREATE_NEW_CONSOLE as NEW_CONSOLE\n"
            "x = NEW_CONSOLE\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_forbids_new_console_imported_from_low_level_module(repo):
    _mk_plugin(
        repo,
        "agent-winapi",
        adopts=False,
        body="from _winapi import CREATE_NEW_CONSOLE as FLAG\nx = FLAG\n",
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_forbids_named_new_console_numeric_constant(repo):
    _mk_plugin(
        repo,
        "agent-numeric",
        adopts=False,
        body="_CREATE_NEW_CONSOLE = 0x00000010\nx = _CREATE_NEW_CONSOLE\n",
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_forbids_new_console_in_vendored_library(repo):
    src = (
        repo / "plugins" / "agent-vendor" / "libs" / "ssh-manager"
        / "src" / "ssh_manager"
    )
    src.mkdir(parents=True)
    (src / "process.py").write_text(
        "import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n",
        encoding="utf-8",
    )
    assert any(
        "plugins/agent-vendor/libs/ssh-manager" in p for p in guard.verify()
    )


def test_forbids_new_console_in_shipped_plugin_script(repo):
    scripts = repo / "plugins" / "agent-script" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "launch.py").write_text(
        "import subprocess\nx = subprocess.CREATE_NEW_CONSOLE\n",
        encoding="utf-8",
    )
    assert any("plugins/agent-script/scripts" in p for p in guard.verify())


def test_production_syntax_error_fails_closed(repo):
    _mk_plugin(repo, "agent-invalid", adopts=False, body="if (\n")
    problems = guard.verify()
    assert any("cannot parse production Python" in p for p in problems)


def test_forbids_annotated_new_console_numeric_constant(repo):
    _mk_plugin(
        repo,
        "agent-annotated",
        adopts=False,
        body="_CREATE_NEW_CONSOLE: int = 0x00000010\nx = _CREATE_NEW_CONSOLE\n",
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_docstring_mention_not_flagged(repo):
    _mk_plugin(repo, "agent-doc", adopts=True,
               body='"""Uses CREATE_NO_WINDOW and DETACHED_PROCESS in prose."""\nx = 1\n')
    assert guard.verify() == []


def test_allow_comment_suppresses(repo):
    _mk_plugin(repo, "agent-ok", adopts=True,
               body="import subprocess\n"
                    "x = subprocess.DETACHED_PROCESS  # headless-guard: allow: daemon\n")
    assert guard.verify() == []


def test_allow_comment_suppresses_interactive_new_console(repo):
    _mk_plugin(
        repo,
        "agent-interactive",
        adopts=False,
        body=(
            "import subprocess\n"
            "x = subprocess.CREATE_NEW_CONSOLE  "
            "# headless-guard: allow interactive terminal\n"
        ),
    )
    assert guard.verify() == []


def test_bare_allow_comment_does_not_suppress(repo):
    _mk_plugin(
        repo,
        "agent-empty-allow",
        adopts=False,
        body=(
            "import subprocess\n"
            "x = subprocess.CREATE_NEW_CONSOLE  # headless-guard: allow\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


@pytest.mark.parametrize("directive", ["allowed", "allowance"])
def test_allow_prefix_word_does_not_suppress(repo, directive):
    _mk_plugin(
        repo,
        f"agent-{directive}",
        adopts=False,
        body=(
            "import subprocess\n"
            f"x = subprocess.CREATE_NEW_CONSOLE  # headless-guard: {directive}\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_allow_text_in_string_does_not_suppress(repo):
    _mk_plugin(
        repo,
        "agent-string-allow",
        adopts=False,
        body=(
            'note = "headless-guard: allow interactive"\n'
            "import subprocess\n"
            "x = subprocess.CREATE_NEW_CONSOLE\n"
        ),
    )
    assert any("CREATE_NEW_CONSOLE" in p for p in guard.verify())


def test_getattr_string_flag_flagged(repo):
    _mk_plugin(repo, "agent-ga", adopts=True,
               body='import subprocess\n'
                    'x = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)\n')
    assert any("CREATE_NEW_PROCESS_GROUP" in p for p in guard.verify())
