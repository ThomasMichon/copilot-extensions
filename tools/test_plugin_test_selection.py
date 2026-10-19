"""Exact-node admission and real contained execution contracts."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
_previous_path = sys.path.copy()
sys.path.insert(0, str(TOOLS))
try:
    from plugin_test_selection import load_selection, selection_diagnostic, validate_test_file

    spec = importlib.util.spec_from_file_location("exact_selection_runner", TOOLS / "run-plugin-tests.py")
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
finally:
    sys.path[:] = _previous_path


@pytest.fixture
def selection_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_selected(value): assert value in (1, 2)\n"
        "@pytest.mark.guard\n"
        "def test_guard(): pass\n"
        "def test_unselected(): raise AssertionError('must not run')\n",
        encoding="utf-8",
    )
    (tests / "test_changed.py").write_text("def test_new(): pass\n", encoding="utf-8")
    path = tmp_path / "selection.json"
    path.write_text(json.dumps({
        "version": 1, "plugin": "fixture",
        "nodes": ["tests/test_sample.py::test_selected[2]"],
        "files": ["tests/test_changed.py"], "include_guards": True,
    }), encoding="utf-8")
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    def fixture_python(*_args, **_kwargs) -> Path:
        environment = tmp_path / "venv"
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        result = subprocess.run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            capture_output=True, text=True, check=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        site = Path(result.stdout.strip())
        # The parent may use a user-installed pytest, which the isolated HOME
        # intentionally hides. Expose its read-only runtime, not host state.
        (site / "test-runtime.pth").write_text(
            "\n".join(path for path in sys.path if path and Path(path).is_dir()) + "\n",
            encoding="utf-8",
        )
        return python

    monkeypatch.setattr(runner, "_ensure_venv", fixture_python)
    return tmp_path, path


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"plugin": "other"}, {"nodes": []},
    {"nodes": ["tests/test_sample.py"]}, {"nodes": ["../test_escape.py::test_x"]},
    {"nodes": ["tests/test_missing.py::test_x"]},
    {"nodes": ["tests\\test_sample.py::test_x"]},
    {"nodes": ["tests/test_sample.py::test_x\nbad"]},
    {"nodes": ["tests/test_sample.py::test_selected[2]"] * 2},
    {"nodes": "not-a-list"}, {"include_guards": "true"},
    {"files": ["tests/test_missing.py"]},
])
def test_invalid_selection_fails_before_preparation(selection_fixture, monkeypatch, change):
    root, path = selection_fixture
    payload = json.loads(path.read_text())
    payload.update(change)
    if change == {"nodes": []}:
        payload["files"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(runner, "_ensure_venv", lambda *_a, **_k: pytest.fail("prepared invalid selection"))
    with pytest.raises(ValueError):
        load_selection(path, "fixture", root)
    with pytest.raises(ValueError):
        runner.run_plugin(
            "fixture", "unused", reinstall=False, kexpr=None,
            limits=runner.Limits(), plugin_timeout=60, test_timeout=10,
            max_files_per_subsuite=25, selection_file=path,
        )


def test_missing_manifest_fails_before_preparation(selection_fixture, monkeypatch):
    root, _ = selection_fixture
    monkeypatch.setattr(runner, "_ensure_venv", lambda *_a, **_k: pytest.fail("prepared invalid selection"))
    with pytest.raises(FileNotFoundError):
        runner.run_plugin(
            "fixture", "unused", reinstall=False, kexpr=None, limits=runner.Limits(),
            plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
            selection_file=root / "missing.json",
        )


@pytest.mark.parametrize("value", [
    "C:/tests/test_sample.py", "C:tests/test_sample.py", "c:/tests/test_sample.py",
    "//server/share/tests/test_sample.py", "C:\\tests\\test_sample.py",
    "\\\\server\\share\\tests\\test_sample.py",
])
def test_windows_drive_and_unc_forms_are_rejected_before_filesystem_resolution(
    selection_fixture, monkeypatch, value,
):
    root, _ = selection_fixture
    monkeypatch.setattr(
        Path, "resolve", lambda *_a, **_k: pytest.fail("resolved a non-relative test path"),
    )
    with pytest.raises(ValueError, match="existing, unlinked"):
        validate_test_file(root, value)


@pytest.mark.parametrize("node", ["test_selected[2]", "test_renamed", "test_selected[99]", "test_selected"])
def test_real_contained_selection_checks_nodes_and_unions_guards_and_files(
    selection_fixture, node: str, capfd,
):
    _, path = selection_fixture
    payload = json.loads(path.read_text())
    payload["nodes"] = ["tests/test_sample.py::" + node]
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.run_plugin(
        "fixture", "unused", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
        selection_file=path,
    )
    assert result == (0 if node == "test_selected[2]" else 4)
    output = capfd.readouterr()
    if result == 0:
        assert "3 passed, 2 deselected" in output.out
    else:
        assert "selected test nodes were not collected" in output.err


@pytest.mark.parametrize("retired", ["test_new", "test_new[old]"])
def test_whole_changed_file_executes_all_replacements_for_retired_baseline_nodes(
    selection_fixture, capfd, retired,
):
    root, path = selection_fixture
    (root / "tests/test_changed.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_replacement(value): assert value in (1, 2)\n",
        encoding="utf-8",
    )
    payload = json.loads(path.read_text())
    payload["nodes"].append("tests/test_changed.py::" + retired)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert runner.run_plugin(
        "fixture", "unused", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
        selection_file=path,
    ) == 0
    assert "4 passed, 2 deselected" in capfd.readouterr().out

def test_selected_node_cannot_be_silently_deselected_by_ambient_options(selection_fixture, monkeypatch):
    root, path = selection_fixture
    (root / "pytest.ini").write_text("[pytest]\naddopts=-k test_guard\n", encoding="utf-8")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_guard")
    assert runner.run_plugin(
        "fixture", "unused", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
        selection_file=path,
    ) == 0


def test_empty_selected_file_is_not_silently_ignored(selection_fixture, capfd):
    root, path = selection_fixture
    (root / "tests/test_changed.py").write_text("# no tests\n", encoding="utf-8")
    assert runner.run_plugin(
        "fixture", "unused", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25, selection_file=path,
    ) == 4
    assert "selected test files collected no tests" in capfd.readouterr().err


def test_exact_nested_suite_never_uses_the_ordinary_no_suite_skip(selection_fixture, monkeypatch):
    monkeypatch.setattr(runner, "_has_suite", lambda _name: False)
    _, path = selection_fixture
    assert runner.run_plugin(
        "fixture", "unused", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25, selection_file=path,
    ) == 0


@pytest.mark.parametrize("plugin", [None, "", "   "])
def test_exact_pytest_option_requires_a_targeted_plugin_name(selection_fixture, plugin):
    from tools import pytest_test_selection

    _, path = selection_fixture

    class Config:
        def getoption(self, option):
            return {"--exact-selection": path, "--selection-plugin": plugin}[option]

    with pytest.raises(pytest.UsageError, match="requires a nonempty --selection-plugin"):
        pytest_test_selection.pytest_collection_modifyitems(Config(), [])


def test_missing_node_failure_reports_count_and_bounded_samples(selection_fixture, monkeypatch):
    from tools import pytest_test_selection

    root, path = selection_fixture
    payload = json.loads(path.read_text())
    payload["nodes"] = [
        f"tests/test_sample.py::test_missing_{index}[{'x' * 500}]"
        for index in range(3000)
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(root)

    class Config:
        def getoption(self, option):
            return {"--exact-selection": path, "--selection-plugin": "fixture"}[option]

    with pytest.raises(pytest.UsageError) as error:
        pytest_test_selection.pytest_collection_modifyitems(Config(), [])
    message = str(error.value)
    assert "selected test nodes were not collected (3000)" in message
    assert "test_missing_0[" in message
    assert "test_missing_2999[" not in message
    assert "... 2990 more" in message
    assert "selection manifest / decision artifact" in message
    assert "x" * 300 not in message
    assert len(message) < 3000


def test_invalid_input_and_empty_file_diagnostics_share_the_bound():
    message = selection_diagnostic(
        "selected test files collected no tests",
        (f"tests/test_{index}_{'x' * 500}.py" for index in range(3000)),
    )
    assert "(3000)" in message and "... 2990 more" in message
    assert len(message) < 3000
    with pytest.raises(ValueError) as error:
        validate_test_file(Path.cwd(), "tests/" + "x" * 5000 + "/../test_bad.py")
    assert len(str(error.value)) < 500


@pytest.mark.parametrize("arguments", [
    ["--all"], ["--changed"], ["fixture", "other"], ["fixture", "--prepare-only"],
    ["fixture", "--collect-only"], ["fixture", "--guards"], ["fixture", "-k", "test"],
    ["fixture", "--test-file", "tests/test_sample.py"],
    ["fixture", "--list"], ["fixture", "--exclude", "fixture"], ["fixture", "--pre-push"],
])
def test_cli_refuses_conflicting_selection_modes(arguments):
    with pytest.raises(SystemExit) as error:
        runner.main([*arguments, "--selection-file", "selection.json"])
    assert error.value.code == 2
