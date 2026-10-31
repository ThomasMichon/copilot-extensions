"""Preserve an admitted physical source namespace without changing ordinary loads."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PureWindowsPath
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
