"""Tests for tools/compat-root-migration.py.

Exercises both modes (``--name``/``--progress``) against a small, synthetic
plugin tree under ``tmp_path`` -- never the real repo -- covering the regex
edge cases that have already needed fixes once: whole-identifier alias
matching (not a suffix match on an unrelated longer identifier), indented/
local root imports, the accessor-call vs. plain-alias-call shapes,
multi-line ``monkeypatch.setattr``, dotted-string ``unittest.mock.patch``
targets, and the two "reject vacuous success" guards (an unresolvable
``--plugin``, an unknown ``--name``).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent / "compat-root-migration.py"
_spec = importlib.util.spec_from_file_location("compat_root_migration", _MODULE_PATH)
crm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crm)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(crm, "REPO", tmp_path)
    return tmp_path


def _make_plugin(repo: Path, plugin: str = "demo-plugin", package: str = "demo_plugin") -> tuple[Path, Path]:
    src = repo / "plugins" / plugin / "src" / package
    tests = repo / "plugins" / plugin / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    return src, tests


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# -- --plugin validation ------------------------------------------------

def test_main_rejects_unresolvable_plugin(repo: Path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        crm.main(["--plugin", "no-such-plugin", "--progress"])
    assert exc_info.value.code == 2
    assert "no-such-plugin" in capsys.readouterr().err


def test_main_accepts_resolvable_plugin(repo: Path, capsys):
    _make_plugin(repo)
    rc = crm.main(["--plugin", "demo-plugin", "--progress"])
    assert rc == 0


# -- --name: reject an unknown name as vacuous success -------------------

def test_cmd_name_rejects_completely_unknown_name(repo: Path, capsys):
    src, _tests = _make_plugin(repo)
    _write(src / "mod.py", "x = 1\n")
    rc = crm.cmd_name("demo-plugin", "_totally_unknown_name")
    assert rc == 1
    assert "no definition site" in capsys.readouterr().err


def test_cmd_name_reports_done_when_definition_exists_but_no_calls(repo: Path):
    src, _tests = _make_plugin(repo)
    _write(src / "output.py", "def _json_output(data):\n    pass\n")
    rc = crm.cmd_name("demo-plugin", "_json_output")
    assert rc == 0


# -- whole-identifier alias matching (regression: `this_platform.lower()`
#    must never count as an `m.lower()` root call just because the alias
#    "m" is a suffix of "platform") ----------------------------------------

def test_find_core_call_sites_does_not_match_alias_suffix(repo: Path):
    src, _tests = _make_plugin(repo)
    _write(
        src / "mod.py",
        "from . import __main__ as m\n"
        "\n"
        "def f(this_platform):\n"
        "    return this_platform.lower()\n",
    )
    hits = crm.find_core_call_sites("demo-plugin", "lower")
    assert hits == []


def test_find_core_call_sites_matches_real_alias_call(repo: Path):
    src, _tests = _make_plugin(repo)
    _write(
        src / "mod.py",
        "from . import __main__ as m\n"
        "\n"
        "def f():\n"
        "    return m._json_output({})\n",
    )
    hits = crm.find_core_call_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


def test_find_core_call_sites_matches_accessor_call_shape(repo: Path):
    src, _tests = _make_plugin(repo)
    _write(
        src / "mod.py",
        "def _core():\n"
        "    from . import __main__ as core\n"
        "    return core\n"
        "\n"
        "def f():\n"
        "    return _core()._json_output({})\n",
    )
    hits = crm.find_core_call_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


# -- indented/local root imports (inside a function body, not module level) --

def test_root_aliases_detected_when_import_is_indented(repo: Path):
    src, _tests = _make_plugin(repo)
    text = (
        "def f():\n"
        "    from agent_worktrees import __main__ as m\n"
        "    return m._json_output({})\n"
    )
    _write(src / "mod.py", text)
    aliases = crm._root_aliases_in_file(text)
    assert aliases == {"m"}
    hits = crm.find_core_call_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


# -- monkeypatch.setattr: single-line and multi-line ----------------------

def test_find_monkeypatch_sites_single_line(repo: Path):
    _src, tests = _make_plugin(repo)
    _write(
        tests / "test_mod.py",
        "from demo_plugin import __main__ as m\n"
        "\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(m, \"_json_output\", lambda *_: None)\n",
    )
    hits = crm.find_monkeypatch_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


def test_find_monkeypatch_sites_multi_line(repo: Path):
    _src, tests = _make_plugin(repo)
    _write(
        tests / "test_mod.py",
        "from demo_plugin import __main__ as m\n"
        "\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        "        m,\n"
        "        \"_json_output\",\n"
        "        lambda *_: None,\n"
        "    )\n",
    )
    hits = crm.find_monkeypatch_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


# -- unittest.mock.patch("<pkg>.__main__.<name>") dotted-string targets ---

def test_find_monkeypatch_sites_detects_dotted_string_patch(repo: Path):
    _src, tests = _make_plugin(repo)
    _write(
        tests / "test_mod.py",
        "from unittest.mock import patch\n"
        "\n"
        "def test_x():\n"
        "    with patch(\"demo_plugin.__main__._json_output\") as fake:\n"
        "        pass\n",
    )
    hits = crm.find_monkeypatch_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


def test_find_monkeypatch_sites_dotted_patch_is_alias_independent(repo: Path):
    """A dotted-string patch target is a literal string, not an alias
    reference -- it must be detected even in a file with NO
    `from . import __main__ as X` import at all."""
    _src, tests = _make_plugin(repo)
    _write(
        tests / "test_mod.py",
        "from unittest.mock import patch\n"
        "\n"
        "def test_x():\n"
        "    with patch(\"demo_plugin.__main__._json_error\") as fake:\n"
        "        pass\n",
    )
    hits = crm.find_monkeypatch_sites("demo-plugin", "_json_error")
    assert len(hits) == 1


def test_find_monkeypatch_sites_detects_dotted_string_setattr(repo: Path):
    """pytest's `monkeypatch.setattr` also accepts a single dotted-string
    target (resolved internally), not just an (object, "attr") pair --
    a third shape distinct from both unittest.mock.patch and the plain
    (alias, "name") fixture call."""
    _src, tests = _make_plugin(repo)
    _write(
        tests / "test_mod.py",
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        "        \"demo_plugin.__main__._json_output\",\n"
        "        lambda *_: None,\n"
        "    )\n",
    )
    hits = crm.find_monkeypatch_sites("demo-plugin", "_json_output")
    assert len(hits) == 1


# -- --progress aggregate scan honors the same whole-identifier fix -------

def test_cmd_progress_excludes_alias_suffix_false_positive(repo: Path, capsys):
    src, _tests = _make_plugin(repo)
    _write(
        src / "mod.py",
        "from . import __main__ as m\n"
        "\n"
        "def f(this_platform):\n"
        "    return this_platform.lower()\n",
    )
    rc = crm.cmd_progress("demo-plugin")
    assert rc == 0
    out = capsys.readouterr().out
    assert "lower:" not in out


def test_cmd_progress_counts_dotted_string_setattr(repo: Path, capsys):
    _src, tests = _make_plugin(repo)
    _write(
        tests / "test_mod.py",
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        "        \"demo_plugin.__main__._json_output\",\n"
        "        lambda *_: None,\n"
        "    )\n",
    )
    rc = crm.cmd_progress("demo-plugin")
    assert rc == 0
    out = capsys.readouterr().out
    assert "distinct names monkeypatched on the root module: 1" in out

