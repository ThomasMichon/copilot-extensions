from __future__ import annotations

from pathlib import Path
import sys

import pytest

from agent_dispatch.source_build import effective_runtime_version

pytestmark = pytest.mark.guard
PACKAGE = "1.2.3-dev4"
SOURCE = f"{PACKAGE}+source.{'0' * 39}1.{'0' * 15}2"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("suffix", ["", f".profile.{'c' * 16}", f".profile.{'c' * 16}.repair.{'d' * 32}"])
def test_installed_source_slot_reports_exact_opaque_identity(tmp_path: Path, nested, suffix) -> None:
    generation = SOURCE + suffix
    slot = tmp_path / "versions" / generation
    prefix = slot / ".venv" if nested else slot
    assert effective_runtime_version(
        "1.2.3.dev4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == generation


@pytest.mark.parametrize("version", ["1.2.3-dev4", "9.0.0"])
def test_checkout_cannot_claim_installed_source(tmp_path: Path, version: str) -> None:
    prefix = tmp_path / "versions" / SOURCE
    assert effective_runtime_version(
        version, prefix=prefix, module_path=tmp_path / "checkout" / "source_build.py",
    ) == version


def test_foreign_package_version_cannot_claim_generation(tmp_path: Path) -> None:
    prefix = tmp_path / "versions" / SOURCE
    assert effective_runtime_version(
        "9.0.0", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == "9.0.0"


@pytest.mark.parametrize("qualified", [False, True])
def test_standalone_wrapper_metadata_qualifies_only_its_own_source_slot(tmp_path, monkeypatch, qualified):
    from importlib import metadata
    import agent_dispatch
    from agent_dispatch import _build_info, source_build

    plugin_version = "9.8.7.dev6"
    generation = SOURCE + f".profile.{'c' * 16}"
    prefix = tmp_path / "versions" / generation / ".venv"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    module = prefix / "lib" / "source_build.py" if qualified else tmp_path / "checkout" / "source_build.py"
    monkeypatch.setattr(source_build, "__file__", str(module))
    monkeypatch.setitem(_build_info.BUILD_INFO, "version", "")
    monkeypatch.setattr(metadata, "version", lambda name: {
        "agent-dispatch": plugin_version, "agent-dispatch-service": "1.2.3.dev4",
    }[name])
    assert effective_runtime_version(agent_dispatch._resolve_version()) == (
        generation if qualified else plugin_version
    )


@pytest.mark.parametrize("suffix", [".profile.bad", f".repair.{'a' * 31}", ".extra"])
def test_malformed_source_profile_does_not_claim_generation(tmp_path, suffix):
    prefix = tmp_path / "versions" / (SOURCE + suffix) / ".venv"
    assert effective_runtime_version(
        "1.2.3.dev4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == "1.2.3.dev4"
