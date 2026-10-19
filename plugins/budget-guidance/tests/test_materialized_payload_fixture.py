"""Installer fixtures preserve either declared dependency source layout."""

from __future__ import annotations

from pathlib import Path

import pytest

import test_installers as installers


pytestmark = pytest.mark.guard
PACKAGE_INIT = Path("src") / "agent_procutil" / "__init__.py"
TOOLS = Path(__file__).resolve().parents[3] / "tools"


def _write_plugin_manifest(plugin: Path, *, embedded: bool) -> None:
    reference = (
        'path = "libs/agent-procutil"'
        if embedded else 'path = "../../libs/agent-procutil", editable = true'
    )
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "budget-guidance"\nversion = "0.0.0"\n'
        f"[tool.uv.sources]\nagent-procutil = {{ {reference} }}\n",
        encoding="utf-8",
    )


def _write_dependency(root: Path, origin: str) -> None:
    (root / PACKAGE_INIT).parent.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "agent-procutil"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (root / PACKAGE_INIT).write_text(f'ORIGIN = "{origin}"\n', encoding="utf-8")


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    return {
        path.relative_to(root): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }


@pytest.fixture
def plugin_source(tmp_path: Path, monkeypatch) -> Path:
    plugin = tmp_path / "repo" / "plugins" / "budget-guidance"
    plugin.mkdir(parents=True)
    (plugin / "payload-marker").write_bytes(b"owning payload")
    monkeypatch.setattr(installers, "PLUGIN", plugin)
    monkeypatch.syspath_prepend(str(TOOLS))
    return plugin


def test_development_fixture_copies_shared_dependency_without_mutating_source(
    plugin_source: Path, tmp_path: Path,
):
    shared = plugin_source.parents[1] / "libs" / "agent-procutil"
    _write_plugin_manifest(plugin_source, embedded=False)
    _write_dependency(shared, "shared")
    before = _snapshot(plugin_source.parents[1])
    target = tmp_path / "payload"

    installers._copy_payload(target)

    assert (target / "libs" / "agent-procutil" / PACKAGE_INIT).read_bytes() == (
        shared / PACKAGE_INIT
    ).read_bytes()
    assert (target / "payload-marker").read_bytes() == b"owning payload"
    assert _snapshot(plugin_source.parents[1]) == before


@pytest.mark.parametrize("shared_present", [False, True])
def test_materialized_fixture_preserves_embedded_dependency(
    plugin_source: Path, tmp_path: Path, shared_present: bool,
):
    embedded = plugin_source / "libs" / "agent-procutil"
    _write_plugin_manifest(plugin_source, embedded=True)
    _write_dependency(embedded, "embedded")
    if shared_present:
        _write_dependency(plugin_source.parents[1] / "libs" / "agent-procutil", "shared")
    before = _snapshot(plugin_source.parents[1])
    target = tmp_path / "payload"

    installers._copy_payload(target)

    assert (target / "libs" / "agent-procutil" / PACKAGE_INIT).read_bytes() == (
        embedded / PACKAGE_INIT
    ).read_bytes()
    assert _snapshot(plugin_source.parents[1]) == before


@pytest.mark.parametrize("missing", ["manifest", "package"])
def test_malformed_embedded_fixture_cannot_fall_back_to_shared_source(
    plugin_source: Path, tmp_path: Path, missing: str,
):
    embedded = plugin_source / "libs" / "agent-procutil"
    _write_plugin_manifest(plugin_source, embedded=True)
    _write_dependency(embedded, "embedded")
    _write_dependency(plugin_source.parents[1] / "libs" / "agent-procutil", "shared")
    required = Path("pyproject.toml") if missing == "manifest" else PACKAGE_INIT
    (embedded / required).unlink()
    before = _snapshot(plugin_source.parents[1])

    with pytest.raises(AssertionError, match=f"Missing agent-procutil fixture {missing}"):
        installers._copy_payload(tmp_path / "payload")

    assert _snapshot(plugin_source.parents[1]) == before


@pytest.mark.parametrize("embedded", [False, True])
def test_fixture_omits_its_own_tests_and_caches_but_preserves_payload_inputs(
    plugin_source: Path, tmp_path: Path, embedded: bool,
):
    _write_plugin_manifest(plugin_source, embedded=embedded)
    dependency = (
        plugin_source / "libs" / "agent-procutil" if embedded
        else plugin_source.parents[1] / "libs" / "agent-procutil"
    )
    _write_dependency(dependency, "owning")
    inputs = {
        plugin_source / "scripts" / "install.ps1": b"native installer\r\n",
        plugin_source / "src" / "budget_guidance" / "__init__.py": b"package\xff",
        dependency / "tests" / "retained.bin": b"\x00\xff\r\n",
    }
    for path, content in inputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for directory in ("tests", "__pycache__", ".pytest_cache"):
        (plugin_source / directory).mkdir()
        (plugin_source / directory / "irrelevant").write_bytes(b"not a build input")
    before = _snapshot(plugin_source.parents[1])
    target = tmp_path / "payload"

    installers._copy_payload(target)

    assert (target / "scripts" / "install.ps1").read_bytes() == inputs[
        plugin_source / "scripts" / "install.ps1"
    ]
    assert (target / "src" / "budget_guidance" / "__init__.py").read_bytes() == b"package\xff"
    assert (target / "libs" / "agent-procutil" / "tests" / "retained.bin").read_bytes() == b"\x00\xff\r\n"
    for directory in ("tests", "__pycache__", ".pytest_cache"):
        assert not (target / directory).exists()
    assert _snapshot(plugin_source.parents[1]) == before


def test_fixture_propagates_copy_worker_failure(plugin_source: Path, tmp_path: Path, monkeypatch):
    def fail_copy(*args, **kwargs):
        raise OSError("fixture copy refused")

    monkeypatch.setattr(installers.shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="fixture copy refused"):
        installers._copy_payload(tmp_path / "payload")
