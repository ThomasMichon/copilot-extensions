"""Discovery completeness travels with the declarations it actually observed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dropin_registry import ScanAuthority

from agent_dispatch.registrar_discovery import (
    Pointer, RegistrarSources, discover_trusted, save_pointers,
)


def _write(directory, name="worker"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"name": name}), encoding="utf-8")
    return path


def test_empty_and_missing_sources_keep_two_item_discovery_contract(tmp_path):
    empty, missing = tmp_path / "empty", tmp_path / "missing"
    empty.mkdir()
    result = discover_trusted([
        Pointer("empty", str(empty)), Pointer("missing", str(missing)),
    ])
    authority, declarations = result
    assert authority is ScanAuthority.COMPLETE and declarations == []
    assert len(result) == 2 and result[0] is authority and result[1] is declarations
    assert result[-1] is declarations and result[:] == (authority, declarations)
    assert [(item.pointer_name, item.status) for item in result.source_observations] == [
        ("empty", "complete"), ("missing", "missing"),
    ]
    assert result.source_observations[0].directories == (empty,)
    assert result.source_observations[1].directories == (missing,)


@pytest.mark.parametrize("failure", ["read", "malformed"])
def test_failed_aggregate_retains_declarations_but_not_partial_completeness(
    tmp_path, monkeypatch, failure,
):
    first, second = tmp_path / "first", tmp_path / "second"
    first_document, second_document = _write(first, "one"), _write(second, "two")
    base = tmp_path / "registry"
    save_pointers([Pointer("first", str(first)), Pointer("second", str(second))], base)
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "dropins", activation_source=lambda: None,
    )
    initial = sources.refresh(emit_warnings=False)
    assert [item.name for item in initial.combined.declarations] == ["one", "two"]
    first_document.unlink()
    if failure == "malformed":
        second_document.write_text("{", encoding="utf-8")
    else:
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == second_document:
                raise PermissionError("test source unavailable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
    report = sources.refresh(emit_warnings=False)
    assert report.trusted_authority is ScanAuthority.INDETERMINATE
    assert report.combined.declarations == initial.combined.declarations
    assert [(item.pointer_name, item.status) for item in report.source_observations] == [
        ("first", "error"), ("second", "error"),
    ]


def test_vanished_entry_does_not_authorize_empty_source(tmp_path, monkeypatch):
    document = _write(tmp_path)
    original = Path.lstat

    def inspect(path, *args, **kwargs):
        if path == document:
            raise FileNotFoundError("test entry vanished after enumeration")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inspect)
    result = discover_trusted([Pointer("source", str(tmp_path))])
    assert result.declarations == []
    assert result.source_observations[0].status == "error"


@pytest.mark.parametrize("layered", [False, True])
@pytest.mark.parametrize("base_present", [False, True])
def test_overlay_does_not_conceal_missing_repository_source(
    tmp_path, monkeypatch, layered, base_present,
):
    from agent_dispatch import configuration_layers_adapter as adapter, repo_config

    root = tmp_path / "repo"
    root.mkdir()
    canonical = root / repo_config.CANONICAL_REPO_CONFIG_DIR / "registrar"
    overlay = root / repo_config.MARKETPLACE_OVERLAYS_DIR / "selected" / "registrar"
    _write(overlay)
    if base_present:
        canonical.mkdir(parents=True)
    monkeypatch.setattr(repo_config, "installation_marketplace_id", lambda: "selected")
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "example", "execution_machine": "test-machine",
            "installation_cell": "test-cell", "binding": "unbound", "base_root": str(root),
        },
    }
    monkeypatch.setattr(adapter, "configuration_report", lambda root: report if layered else None)
    monkeypatch.setattr(adapter, "install_dir", lambda: tmp_path / "machine")
    result = discover_trusted([
        Pointer("repository", str(root), kind="repo", owner="repo:example", aliases=("example/repo",)),
    ])
    assert [item.name for item in result.declarations] == ["worker"]
    observation, = result.source_observations
    assert observation.status == ("complete" if base_present else "missing")
    assert canonical in observation.directories and overlay in observation.directories
    assert observation.namespace == "repo:example" and observation.aliases == ("example/repo",)
    assert observation.root == root.resolve() and observation.declaration_names == ("worker",)
