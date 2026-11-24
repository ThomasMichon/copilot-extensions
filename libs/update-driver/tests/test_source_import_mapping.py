import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent_procutil
from update_driver import runtime_command


def test_source_mode_module_child_imports_from_caller_source_roots(
    area: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "toolkit-source"
    (source / "toolkit_fixture").mkdir(parents=True)
    (source / "toolkit_fixture" / "__init__.py").write_text("", encoding="utf-8")
    (source / "toolkit_fixture" / "__main__.py").write_text(
        "import sys\nprint('worker-imported', *sys.argv[1:])\n", encoding="utf-8",
    )
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.syspath_prepend(str(source))
    argv, env = runtime_command("toolkit_fixture", ("worker",))
    assert argv[1:] == ("-m", "toolkit_fixture", "worker")
    assert str(source.resolve()) in env["PYTHONPATH"].split(os.pathsep)
    ambient = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        argv, env={**ambient, **env}, cwd=area, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "worker-imported worker"


def test_interpreter_owned_module_child_needs_no_import_mapping(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {}))
    assert runtime_command("json", ())[1] == {}
