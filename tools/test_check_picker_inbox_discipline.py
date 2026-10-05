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


def test_flags_a_module_level_alias_used_from_a_nested_function(repo):
    """A nested function is a real Python closure: it genuinely resolves
    a module-level (or enclosing-function) alias at runtime, so the guard
    must flag it too -- starting every function with a wholly empty alias
    set would miss this exact call."""
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_model.py",
        "marshal = app.call_from_thread\n"
        "def worker():\n"
        "    marshal(fn)\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_flags_an_outer_function_alias_used_from_a_nested_inner_function(repo):
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_regions.py",
        "def outer(self):\n"
        "    marshal = self.app.call_from_thread\n"
        "    def inner():\n"
        "        marshal(fn)\n"
        "    inner()\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_a_nested_functions_own_parameter_shadows_an_inherited_alias(repo):
    """A nested function's own parameter of the same name as an outer
    alias is a fresh, unrelated binding -- it must NOT inherit the outer
    scope's alias meaning just because the name matches."""
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_pivots.py",
        "def outer(self):\n"
        "    marshal = self.app.call_from_thread\n"
        "    def inner(marshal):\n"
        "        marshal(fn)\n"
        "    inner(some_safe_callable)\n",
    )
    assert guard.verify() == []


def test_conditional_reassignment_does_not_clear_an_alias_afterward(repo):
    """A reassignment inside ONE branch of a conditional must not
    permanently clear an alias for code that runs after it -- some other
    branch (or neither) might actually execute at runtime, so the alias
    could still be live. Conservative merging must keep flagging the call
    after the conditional."""
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_maintenance_actions.py",
        "def f(self, use_safe):\n"
        "    marshal = self.app.call_from_thread\n"
        "    if use_safe:\n"
        "        marshal = some_safe_callable\n"
        "    marshal(fn)\n",
    )
    assert any("call_from_thread(" in p for p in guard.verify())


def test_alias_in_one_function_does_not_flag_an_unrelated_name_in_another(repo):
    """An alias assigned inside one function must not leak into a sibling
    function -- a parameter or local that merely happens to share the
    same name (``marshal``) there, unrelated to call_from_thread, must
    never be flagged."""
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_focus.py",
        "def f(self):\n"
        "    marshal = self.app.call_from_thread\n"
        "    marshal(fn)\n"
        "\n"
        "def g(self, marshal):\n"
        "    marshal(fn)\n",
    )
    problems = guard.verify()
    assert any("call_from_thread(" in p and ":3:" in p for p in problems)
    assert not any(":6:" in p for p in problems)


def test_reassigning_an_alias_to_a_safe_callable_clears_it(repo):
    """Once an alias name is reassigned to something that is NOT
    call_from_thread, it must stop being flagged -- a stale alias
    tracked forever would reject perfectly safe code reusing that name."""
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_selection.py",
        "def f(self):\n"
        "    marshal = self.app.call_from_thread\n"
        "    marshal = some_safe_callable\n"
        "    marshal(fn)\n",
    )
    assert guard.verify() == []


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


def test_allow_comment_suppresses_a_multiline_call(repo):
    """The opening line of a call whose arguments continue on later lines
    has an unmatched open parenthesis if tokenized in isolation -- the
    escape hatch must still work there, not silently fail closed."""
    d = _picker_tui_dir(repo)
    _write(
        d,
        "engine_live_screens.py",
        "self.app.call_from_thread(  # inbox-guard: allow multiline shim\n"
        "    fn,\n"
        ")\n",
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
