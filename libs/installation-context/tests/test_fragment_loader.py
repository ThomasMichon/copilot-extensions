"""Preserve an admitted physical source namespace without changing ordinary loads."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePath, PureWindowsPath
from types import ModuleType
from typing import NoReturn

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "installation_context.py"


@pytest.fixture
def context_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fragment_loader_installation_context", ENTRY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("root", ("GLOBALROOT", "globalroot"))
def test_fragment_directory_does_not_resolve_a_physical_namespace(
    context_module: ModuleType,
    root: str,
) -> None:
    class PhysicalPath(PureWindowsPath):
        def resolve(self, strict: bool = False) -> NoReturn:
            raise AssertionError("Physical source must not be resolved back through DOS.")

    script = PhysicalPath(rf"\\?\{root}\Device\HarddiskVolume3\source\installation_context.py")
    assert context_module._fragment_directory(script) == script.parent


def test_fragment_directory_retains_ordinary_windows_resolution(
    context_module: ModuleType,
) -> None:
    calls: list[bool] = []
    resolved = PureWindowsPath(r"C:\actual-source\installation_context.py")

    class OrdinaryPath(PureWindowsPath):
        def resolve(self, strict: bool = False) -> PureWindowsPath:
            calls.append(strict)
            return resolved

    directory = context_module._fragment_directory(
        OrdinaryPath(r"C:\alias\installation_context.py")
    )
    assert directory == resolved.parent
    assert calls == [False]


def test_fragment_directory_retains_ordinary_relative_resolution(
    context_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    script = Path("relative") / "installation_context.py"
    assert context_module._fragment_directory(script) == (tmp_path / "relative")


def test_fragment_loader_reads_from_the_lexical_physical_parent(
    context_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[tuple[str, str]] = []

    class PhysicalPath(PureWindowsPath):
        def resolve(self, strict: bool = False) -> NoReturn:
            raise AssertionError("Fragment load must not reintroduce a DOS address.")

        def read_text(self, encoding: str) -> str:
            reads.append((str(self), encoding))
            return "bootstrap_fixture_loaded = True\n"

    script = PhysicalPath(r"\\?\GLOBALROOT\Device\HarddiskVolume3\source\installation_context.py")
    monkeypatch.setattr(context_module, "__file__", str(script))
    monkeypatch.setattr(context_module, "Path", lambda value: PhysicalPath(value))
    monkeypatch.setattr(context_module, "_FRAGMENT_FILES", ("fixture.py",))
    context_module._load_fragments()
    assert reads == [(str(script.parent / "fixture.py"), "utf-8")]
    assert context_module.bootstrap_fixture_loaded is True


@pytest.mark.skipif(os.name != "nt", reason="Windows native paths")
@pytest.mark.parametrize(("source", "expected"), [
    (r"C:\fixture\fragment.py", r"\\?\C:\fixture\fragment.py"),
    (r"\\server\share\fragment.py", r"\\?\UNC\server\share\fragment.py"),
    (r"\\?\C:\fixture\fragment.py", r"\\?\C:\fixture\fragment.py"),
    (r"\\?\GLOBALROOT\Device\HarddiskVolume3\fragment.py",
     r"\\?\GLOBALROOT\Device\HarddiskVolume3\fragment.py"),
])
def test_native_fragment_preserves_windows_path_identity(
    context_module: ModuleType, source: str, expected: str,
) -> None:
    assert str(context_module._native_fragment(Path(source))) == expected


def _deep_fragment_directory(directory: PurePath) -> PurePath:
    while len(str(directory)) < 215:
        directory /= "d" * max(1, min(60, 215 - len(str(directory)) - 1))
    return directory


@pytest.mark.parametrize("initial_length", (92, 153, 214))
def test_deep_fragment_directory_progress_is_bounded(initial_length: int) -> None:
    class BoundedWindowsPath(PureWindowsPath):
        joins = 0

        def __truediv__(self, value: str | os.PathLike[str]) -> BoundedWindowsPath:
            type(self).joins += 1
            assert type(self).joins <= 4, "path construction did not make bounded progress"
            assert os.fspath(value), "path construction attempted an empty component"
            return super().__truediv__(value)

    start = BoundedWindowsPath("C:\\" + "d" * (initial_length - 3))
    assert len(str(start)) == initial_length
    directory = PureWindowsPath(_deep_fragment_directory(start))
    assert 215 <= len(str(directory)) <= 216
    entry = directory / "installation_context.py"
    longest = directory / "_installation_context_runtime_slot_ownership.py"
    assert len(str(entry)) < 260 <= len(str(longest))


@pytest.mark.skipif(os.name != "nt", reason="Windows native paths")
def test_fragment_loader_reads_deep_ordinary_windows_path(
    context_module: ModuleType, tmp_path: Path,
) -> None:
    directory = Path(_deep_fragment_directory(tmp_path.resolve()))
    entry = directory / "installation_context.py"
    longest = directory / "_installation_context_runtime_slot_ownership.py"
    assert len(str(entry)) < 260 <= len(str(longest))
    value = str(directory)
    native = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    native.mkdir(parents=True)
    try:
        for name in ("installation_context.py", *context_module._FRAGMENT_FILES):
            shutil.copyfile(ENTRY.parent / name, native / name)
        code = (
            "import importlib.util,sys;"
            "s=importlib.util.spec_from_file_location('fixture_context',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;"
            "s.loader.exec_module(m);print('loaded')"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(entry)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW"),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "loaded"
    finally:
        shutil.rmtree(native)
