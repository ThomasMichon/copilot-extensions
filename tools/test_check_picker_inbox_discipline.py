"""Tests for the Picker inbox-discipline guard.

The guard flags a raw ``call_from_thread(`` call anywhere in the Picker's
own ``picker_tui/`` source (``inbox.py`` itself is exempt -- it IS the
sanctioned wake primitive). It is AST-based (docstrings/comments never
count) and supports an inline ``# inbox-guard: allow <reason>`` escape
hatch.

Run:  python -m pytest tools/test_check_picker_inbox_discipline.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "check-picker-inbox-discipline.py"
_spec = importlib.util.spec_from_file_location(
    "check_picker_inbox_discipline", _SCRIPT
)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _picker_tui_dir(repo: Path) -> Path:
    d = (
        repo / "worktree-manager" / "src" / "worktree_manager"
        / "production_picker" / "picker_tui"
    )
    d.mkdir(parents=True)
    return d


def _write(d: Path, name: str, body: str) -> None:
    (d / name).write_text(body, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(
        guard,
        "PICKER_TUI_DIR",
        tmp_path / "worktree-manager" / "src" / "worktree_manager"
        / "production_picker" / "picker_tui",
    )
    return tmp_path


def test_flags_raw_call_from_thread(repo):
    d = _picker_tui_dir(repo)
    _write(d, "engine_runtime.py", "self.app.call_from_thread(fn)\n")
    problems = guard.verify()
    assert any("engine_runtime.py" in p and "call_from_thread(" in p
               for p in problems)


def test_clean_tree_reports_nothing(repo):
    d = _picker_tui_dir(repo)
    _write(d, "engine.py", "inbox.post('slot', fn)\n")
    assert guard.verify() == []


def test_inbox_module_itself_is_exempt(repo):
    d = _picker_tui_dir(repo)
    _write(d, "inbox.py", "self._owner.call_from_thread(fn)\n")
    assert guard.verify() == []


def test_flags_bare_name_call(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_pivot_actions.py",
        "call_from_thread = self.app.call_from_thread\ncall_from_thread(fn)\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_flags_aliased_call_via_attribute_assignment(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_worktree_actions.py",
        "marshal = self.app.call_from_thread\nmarshal(fn)\n",
    )
    assert any("engine_worktree_actions.py" in p and "call_from_thread(" in p
               for p in guard.verify())


def test_flags_alias_of_an_alias(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_sessions_actions.py",
        "marshal = self.app.call_from_thread\n"
        "also_marshal = marshal\n"
        "also_marshal(fn)\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_docstring_mention_not_flagged(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_loading.py",
        '"""Never call call_from_thread directly -- use Inbox."""\nx = 1\n',
    )
    assert guard.verify() == []


def test_allow_comment_suppresses(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_worker_actions.py",
        "self.app.call_from_thread(fn)  # inbox-guard: allow one-off legacy shim\n",
    )
    assert guard.verify() == []


def test_bare_allow_comment_does_not_suppress(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_dialogs.py",
        "self.app.call_from_thread(fn)  # inbox-guard: allow\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


@pytest.mark.parametrize("directive", ["allowed", "allowance"])
def test_allow_prefix_word_does_not_suppress(repo, directive):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_regions.py",
        f"self.app.call_from_thread(fn)  # inbox-guard: {directive}\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_allow_text_in_string_does_not_suppress(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_helpers.py",
        'note = "inbox-guard: allow legacy"\n'
        "self.app.call_from_thread(fn)\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_production_syntax_error_fails_closed(repo):
    d = _picker_tui_dir(repo)
    _write(d, "engine_broken.py", "if (\n")
    problems = guard.verify()
    assert any("cannot parse Picker source" in p for p in problems)


def test_missing_picker_tui_dir_is_a_clean_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "PICKER_TUI_DIR", tmp_path / "nonexistent")
    import sys
    old_argv = sys.argv
    sys.argv = ["check-picker-inbox-discipline.py"]
    try:
        assert guard.main() == 0
    finally:
        sys.argv = old_argv
    assert "SKIPPED" in capsys.readouterr().out


def test_list_flag_prints_scanned_files(repo, capsys):
    d = _picker_tui_dir(repo)
    _write(d, "engine.py", "x = 1\n")
    _write(d, "inbox.py", "x = 1\n")
    import sys
    old_argv = sys.argv
    sys.argv = ["check-picker-inbox-discipline.py", "--list"]
    try:
        assert guard.main() == 0
    finally:
        sys.argv = old_argv
    out = capsys.readouterr().out
    assert "engine.py" in out
    # inbox.py is exempt from scanning (it IS the primitive), so --list must
    # never name it either.
    assert "inbox.py" not in out
