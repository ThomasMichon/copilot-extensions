import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent_procutil
from update_driver import runtime_command


def _run_child(module: str, source: Path, cwd: Path) -> str:
    argv, env = runtime_command(module, ("worker",))
    assert argv[1:] == ("-m", module, "worker")
    assert str(source.resolve()) in env["PYTHONPATH"].split(os.pathsep)
    ambient = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        argv, env={**ambient, **env}, cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write_package(source: Path, package: str, *, namespace: bool = False) -> None:
    (source / package).mkdir(parents=True)
    if not namespace:
        (source / package / "__init__.py").write_text("", encoding="utf-8")
    worker = "__main__.py" if not namespace else "worker.py"
    (source / package / worker).write_text(
        "import sys\nprint('worker-imported', *sys.argv[1:])\n", encoding="utf-8",
    )


def test_source_mode_module_child_imports_from_caller_source_roots(
    area: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "toolkit-source"
    _write_package(source, "toolkit_fixture")
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.syspath_prepend(str(source))
    assert _run_child("toolkit_fixture", source, area) == "worker-imported worker"


def test_source_mode_namespace_module_child_imports_from_caller_source_roots(
    area: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "namespace-source"
    _write_package(source, "namespace_fixture", namespace=True)
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.syspath_prepend(str(source))
    assert _run_child("namespace_fixture.worker", source, area) == "worker-imported worker"


def test_source_checkout_beneath_interpreter_prefix_still_maps(
    area: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "usr"
    source = prefix / "src" / "toolkit"
    _write_package(source, "prefixed_fixture")
    for name in ("prefix", "base_prefix", "exec_prefix"):
        monkeypatch.setattr(sys, name, str(prefix))
    monkeypatch.syspath_prepend(str(source))
    assert _run_child("prefixed_fixture", source, area) == "worker-imported worker"


def test_interpreter_owned_module_child_needs_no_import_mapping(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {}))
    assert runtime_command("json", ())[1] == {}


@pytest.mark.parametrize("libdir", ["lib", "lib64"])
def test_posix_stdlib_archive_is_interpreter_owned(
    libdir: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zipfile

    prefix = tmp_path / "usr"
    archive = prefix / libdir / f"python{sys.version_info[0]}{sys.version_info[1]}.zip"
    archive.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "platlibdir", libdir, raising=False)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("archived_fixture/__init__.py", "")
        bundle.writestr("archived_fixture/__main__.py", "")
    source = tmp_path / "toolkit-source"
    _write_package(source, "toolkit_fixture")
    for name in ("prefix", "base_prefix", "exec_prefix"):
        monkeypatch.setattr(sys, name, str(prefix))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {}))
    monkeypatch.syspath_prepend(str(archive))
    monkeypatch.syspath_prepend(str(source))
    assert runtime_command("archived_fixture", ())[1] == {}
    roots = runtime_command("toolkit_fixture", ())[1]["PYTHONPATH"].split(os.pathsep)
    assert str(source.resolve()) in roots
    assert str(archive.resolve()) not in roots