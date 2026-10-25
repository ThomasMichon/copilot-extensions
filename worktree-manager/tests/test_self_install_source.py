"""Read-only Manager source selection; construction has a separate native lane."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from worktree_manager import self_install as si


def _payload(directory: Path, commit: str, version: str = "1.2.3") -> tuple[Path, object]:
    directory.mkdir()
    package = directory / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (package / "__main__.py").write_text("")
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "copilot-extensions-worktree-manager"\nversion = "{version}"\n',
    )
    source = si._runtime_installer().runtime_source
    digest = source.assembly_hash(directory)
    identity = source.SourceIdentity(
        "worktree-manager", version, commit, "c" * 40, digest,
        source.runtime_version(version, commit, digest),
    )
    (directory / source.SOURCE_FILE).write_text(json.dumps(identity.document()))
    return directory, identity


@pytest.fixture
def selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "root"
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "bin")
    monkeypatch.setattr(si, "control_plane_providers_dir", lambda: tmp_path / "providers")
    return root


def _select(root: Path, payload: Path, identity, *, repair: bool = False) -> Path:
    adapter = si._runtime_installer()
    generation = identity.runtime_version + (".repair." + "f" * 32 if repair else "")
    slot = si.version_slot(generation, root)
    shutil.copytree(payload, slot / "payload")
    python = slot / ("Scripts/python.exe" if si.os.name == "nt" else "bin/python")
    python.parent.mkdir(exist_ok=True)
    python.write_text("fixture interpreter")
    adapter.runtime.mark_complete(root, generation, payload_hash="a" * 64)
    si._write_marker(root, generation)
    si._deploy_binstubs()
    si._write_control_plane_provider_manifest(payload, root=root)
    return slot


def _bytes(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.parent.rglob("*") if path.is_file()}


@pytest.mark.parametrize("repair", [False, True])
def test_exact_source_plan_reports_actual_selected_generation(selection, tmp_path, repair):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, repair=repair)
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "already-current", result
    assert result.version == "1.2.3"
    assert result.slot == str(slot)
    assert result.marker == slot.name
    assert _bytes(selection) == before


@pytest.mark.parametrize("version", ["1.2.3", "2.0.0"])
def test_different_source_is_planned_even_with_matching_package(selection, tmp_path, version):
    previous, identity = _payload(tmp_path / "previous", "a" * 40, version)
    _select(selection, previous, identity)
    payload, desired = _payload(tmp_path / "payload", "b" * 40)
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert result.slot == str(si.version_slot(desired.runtime_version, selection))
    assert result.marker is None
    assert _bytes(selection) == before


@pytest.mark.parametrize("field", ["version", "completed_at"])
def test_source_selection_requires_valid_completion(selection, tmp_path, field):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity)
    marker = slot / ".install-complete.json"
    document = json.loads(marker.read_text())
    document.pop(field)
    marker.write_text(json.dumps(document))
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert _bytes(selection) == before


@pytest.mark.parametrize("selected", [False, True])
def test_invalid_source_bytes_are_explicit_errors_without_publication(selection, tmp_path, selected):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity)
    damaged = slot / "payload" if selected else payload
    (damaged / "src" / "worktree_manager" / "__main__.py").write_text("changed bytes\n")
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "error", result
    assert "bytes do not match" in result.reason
    assert _bytes(selection) == before


def test_first_source_plan_names_immutable_target_without_creating_root(selection, tmp_path):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert result.slot == str(si.version_slot(identity.runtime_version, selection))
    assert not selection.exists()
