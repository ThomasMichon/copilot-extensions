"""A candidate built by an older installed engine completes its own retention bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import worktree_manager
from worktree_manager import runtime_candidate, self_install

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
# The helper names Manager 0.7.87's installer engine retains during self-update.
OLDER_ENGINE_HELPERS = (
    "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
    "versioned_runtime_source.py", "versioned_runtime_construction.py",
    "versioned_runtime_publication.py",
)


def _older_engine_slot(tmp_path: Path) -> Path:
    slot = tmp_path / "versions" / "1.2.3-dev1"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    for name in OLDER_ENGINE_HELPERS:
        (bootstrap / name).write_bytes((SCRIPTS / name).read_bytes())
    return slot


def test_shipped_scripts_supply_every_required_retention_helper() -> None:
    assert all((SCRIPTS / name).is_file() for name in runtime_candidate.RETENTION_HELPERS)


def test_older_engine_bootstrap_is_completed_from_candidate_source(tmp_path: Path) -> None:
    slot = _older_engine_slot(tmp_path)
    bootstrap = slot / ".runtime-bootstrap"
    (bootstrap / "versioned_runtime.py").write_bytes(b"retained by the older engine\n")

    runtime_candidate._complete_retention_bootstrap(slot, SCRIPTS)

    for name in runtime_candidate.RETENTION_HELPERS:
        if name == "versioned_runtime.py":
            assert (bootstrap / name).read_bytes() == b"retained by the older engine\n"
        else:
            assert (bootstrap / name).read_bytes() == (SCRIPTS / name).read_bytes()


def test_slot_without_process_retention_is_left_unchanged(tmp_path: Path) -> None:
    slot = tmp_path / "versions" / "1.2.3-dev1"
    slot.mkdir(parents=True)
    runtime_candidate._complete_retention_bootstrap(slot, SCRIPTS)
    assert not (slot / ".runtime-bootstrap").exists()


def _verifiable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot: Path) -> None:
    metadata = slot / "Lib" / "site-packages"
    package = metadata / "worktree_manager" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(worktree_manager, "__file__", str(package))
    monkeypatch.setattr(runtime_candidate, "distribution", lambda _name: SimpleNamespace(
        locate_file=lambda _name: metadata, read_text=lambda _name: "{}",
    ))
    monkeypatch.setattr(self_install, "payload_version", lambda _path: worktree_manager.__version__)
    monkeypatch.setitem(sys.modules, "worktree_manager.__main__", ModuleType("worktree_manager.__main__"))


def test_upgrade_from_older_engine_passes_candidate_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = _older_engine_slot(tmp_path)
    _verifiable(tmp_path, monkeypatch, slot)
    with pytest.raises(RuntimeError, match="retention bootstrap is missing versioned_runtime_identity.py"):
        runtime_candidate.verify_runtime_candidate()

    runtime_candidate._complete_retention_bootstrap(slot, SCRIPTS)
    runtime_candidate.verify_runtime_candidate()
