from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest

from agent_codespaces.source_build import effective_runtime_version

pytestmark = pytest.mark.guard
PACKAGE = "1.2.3-dev4"
SOURCE = f"{PACKAGE}+source.{'0' * 39}1.{'0' * 15}2"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("suffix", ["", ".profile." + "a" * 16,
                                   ".profile." + "a" * 16 + ".repair." + "b" * 32])
def test_installed_source_slot_advertises_exact_identity(
    tmp_path: Path, nested: bool, suffix: str,
) -> None:
    identity = SOURCE + suffix
    slot = tmp_path / "versions" / identity
    prefix = slot / ".venv" if nested else slot
    assert effective_runtime_version(
        "1.2.3.dev4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == identity


@pytest.mark.parametrize("version", ["1.2.3-dev4", "9.0.0"])
def test_checkout_cannot_claim_installed_source_identity(tmp_path: Path, version: str) -> None:
    prefix = tmp_path / "versions" / SOURCE
    assert effective_runtime_version(
        version, prefix=prefix, module_path=tmp_path / "checkout" / "source_build.py",
    ) == version


def test_foreign_package_version_cannot_claim_slot(tmp_path: Path) -> None:
    prefix = tmp_path / "versions" / SOURCE
    assert effective_runtime_version(
        "9.0.0", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == "9.0.0"


@pytest.mark.parametrize("nested", [False, True])
def test_owner_capture_uses_physical_slot_retention_bootstrap(tmp_path, monkeypatch, nested):
    from agent_codespaces import source_build

    root = tmp_path / "runtime"
    slot = root / "versions" / SOURCE
    prefix = slot / ".venv" if nested else slot
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    (bootstrap / "versioned_runtime.py").write_text("FIXTURE_SLOT = True\n")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(source_build, "__file__", str(prefix / "lib" / "source_build.py"))
    monkeypatch.setattr(source_build, "_owner_slot_identity", lambda runtime, home, name: (
        "completion" if runtime.FIXTURE_SLOT and home == root and name == SOURCE else None
    ))
    selection = source_build.capture_owner_runtime(root)
    assert selection is not None
    assert selection.identity == "completion"
    assert selection.root == root
    assert Path(selection.runtime.__file__) == bootstrap / "versioned_runtime.py"


@pytest.mark.parametrize("source_slot", [False, True])
@pytest.mark.parametrize("build_info", [False, True])
def test_version_command_retains_build_commit_and_reports_selected_source(
    tmp_path: Path, monkeypatch, capsys, source_slot: bool, build_info: bool,
) -> None:
    from importlib import metadata

    from agent_codespaces import __main__ as cli
    from agent_codespaces import source_build

    prefix = tmp_path / "versions" / SOURCE if source_slot else tmp_path / "ordinary-venv"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(source_build, "__file__", str(prefix / "lib" / "source_build.py"))
    monkeypatch.setattr(metadata, "version", lambda _: PACKAGE.replace("-dev", ".dev"))
    module = ModuleType("agent_codespaces._build_info") if build_info else None
    if module is not None:
        module.BUILD_INFO = {"version": PACKAGE, "commit": "c" * 40}
    monkeypatch.setitem(sys.modules, "agent_codespaces._build_info", module)
    assert cli._cmd_version() == 0
    version = SOURCE if source_slot else (PACKAGE if build_info else PACKAGE.replace("-dev", ".dev"))
    suffix = " (cccccccc)" if build_info else ""
    assert capsys.readouterr().out == f"agent-codespaces {version}{suffix}\n"
