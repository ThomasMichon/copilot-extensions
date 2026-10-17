from __future__ import annotations

from pathlib import Path

import pytest

from agent_mcp.source_build import effective_runtime_version

pytestmark = pytest.mark.guard
PACKAGE = "1.2.3-dev4"
SOURCE = f"{PACKAGE}+source.{'0' * 39}1.{'0' * 15}2"


def test_installed_source_slot_advertises_exact_identity(tmp_path: Path) -> None:
    prefix = tmp_path / "versions" / SOURCE
    assert effective_runtime_version(
        "1.2.3.dev4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == SOURCE


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
