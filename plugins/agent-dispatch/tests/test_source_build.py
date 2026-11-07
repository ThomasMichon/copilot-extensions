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


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("local", ["", "+vendor.007"])
@pytest.mark.parametrize("release", ["1.2.3.dev4", "1.2.3rc4", "1.2.3.post2"])
def test_reconstructed_slot_reports_exact_generation(tmp_path: Path, nested, local, release) -> None:
    package = release + local
    generation = package + ("." if local else "+") + "repair." + "0" * 31 + "1"
    slot = tmp_path / "versions" / generation
    prefix = slot / ".venv" if nested else slot
    module = prefix / "lib" / "source_build.py"
    assert effective_runtime_version(package, prefix=prefix, module_path=module) == generation
    assert effective_runtime_version(
        "9.0.0", prefix=prefix, module_path=module,
    ) == "9.0.0"
    assert effective_runtime_version(
        package, prefix=prefix, module_path=tmp_path / "checkout" / "source_build.py",
    ) == package


@pytest.mark.parametrize("suffix", ["repair.bad", "repair." + "a" * 31, "repair." + "g" * 32])
def test_malformed_reconstructed_slot_keeps_package_version(tmp_path: Path, suffix) -> None:
    prefix = tmp_path / "versions" / (PACKAGE + "+" + suffix)
    assert effective_runtime_version(
        PACKAGE, prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == PACKAGE


def test_reconstructed_slot_rejects_a_second_local_separator(tmp_path: Path) -> None:
    package = "1.2.3+vendor"
    prefix = tmp_path / "versions" / (package + "+repair." + "a" * 32)
    assert effective_runtime_version(
        package, prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == package


@pytest.mark.parametrize(("package", "owner"), [
    ("1.2.3rc4", "1.2.3-rc4"),
    ("1.2.3+vendor.7", "1.2.3+vendor.007"),
])
def test_reconstructed_slot_accepts_equivalent_package_versions(tmp_path: Path, package, owner) -> None:
    generation = owner + ("." if "+" in owner else "+") + "repair." + "a" * 32
    prefix = tmp_path / "versions" / generation
    assert effective_runtime_version(
        package, prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == generation


def test_missing_packaging_does_not_break_runtime_identity(tmp_path: Path, monkeypatch) -> None:
    import builtins

    original = builtins.__import__

    def without_packaging(name, *args, **kwargs):
        if name.startswith("packaging"):
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_packaging)
    generation = "1.2.3+repair." + "a" * 32
    prefix = tmp_path / "versions" / generation
    assert effective_runtime_version(
        "1.2.3", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == generation
    prefix = tmp_path / "versions" / ("1.2.3-rc4+repair." + "a" * 32)
    assert effective_runtime_version(
        "1.2.3rc4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == "1.2.3rc4"


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


def test_reconstructed_slot_does_not_adopt_unrelated_wrapper_metadata(tmp_path, monkeypatch):
    from importlib import metadata
    import agent_dispatch
    from agent_dispatch import _build_info, source_build

    generation = "1.2.3.dev4+repair." + "a" * 32
    prefix = tmp_path / "versions" / generation
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(source_build, "__file__", str(prefix / "lib" / "source_build.py"))
    monkeypatch.setitem(_build_info.BUILD_INFO, "version", "")
    monkeypatch.setattr(metadata, "version", lambda name: {
        "agent-dispatch": "9.8.7", "agent-dispatch-service": "1.2.3.dev4",
    }[name])
    assert agent_dispatch._resolve_version() == "9.8.7"


@pytest.mark.parametrize("suffix", [".profile.bad", f".repair.{'a' * 31}", ".extra"])
def test_malformed_source_profile_does_not_claim_generation(tmp_path, suffix):
    prefix = tmp_path / "versions" / (SOURCE + suffix) / ".venv"
    assert effective_runtime_version(
        "1.2.3.dev4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == "1.2.3.dev4"
