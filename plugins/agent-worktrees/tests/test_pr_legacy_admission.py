"""Physical receiver controls; modeled boot evidence is not a real boot crossing."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from update_driver import boot

from agent_worktrees import config as cfg, pr_authority
from agent_worktrees import pr_legacy_admission as admission

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.publication_state")

SEALED_BOOT = boot.ExecutionDomainBootEvidence(
    1, "windows", "windows-vm-generation-counter", "1" * 32,
    "windows:11111111-1111-4111-8111-111111111111",
)
LATER_BOOT = replace(SEALED_BOOT, boot="windows:22222222-2222-4222-8222-222222222222")


def modeled_seal(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Build only a disposable receiver fixture, not a transition implementation."""
    root.mkdir(parents=True, exist_ok=True)
    historical = root / "pr-rewrite-authority.lock"
    if not historical.exists():
        historical.write_bytes(b"0")
    directory = root / "pr-legacy-admission"
    directory.mkdir()
    historical.rename(directory / "retained.lock")
    historical.mkdir()
    def identity(path: Path) -> list[int]:
        info = path.lstat()
        return [info.st_dev, info.st_ino]
    document: dict[str, object] = {
        "contract": admission.CONTRACT, "phase": "sealed",
        "authority_contract": admission.AUTHORITY_CONTRACT,
        "root": identity(root), "directory": identity(directory),
        "original": identity(directory / "retained.lock"), "barrier": identity(historical),
        "sealed_boot": asdict(SEALED_BOOT),
    }
    (directory / "transition.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(boot, "native_execution_domain_boot_evidence", lambda: LATER_BOOT)
    if os.name != "nt":
        monkeypatch.setattr(admission, "_native_volume_type", lambda _root: 3)
    return document


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "installation"
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    return root, modeled_seal(root, monkeypatch)


def rewrite(root: Path, value: dict[str, object]) -> None:
    (root / "pr-legacy-admission" / "transition.json").write_text(
        json.dumps(value), encoding="utf-8",
    )


@pytest.mark.parametrize("volume_supported", [False, True])
def test_unsealed_installation_keeps_normal_authority_but_refuses_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, volume_supported: bool,
) -> None:
    root = tmp_path / "installation"
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    def volume(_root: Path) -> int:
        if not volume_supported:
            raise admission.LegacyAdmissionError("Fixture native volume provider is unavailable.")
        return 3
    monkeypatch.setattr(admission, "_native_volume_type", volume)
    with pr_authority.guard():
        assert (root / "pr-rewrite-authority.lock").is_file()
    before = tuple(root.iterdir())
    reason = "not physically qualified" if volume_supported else "native volume provider is unavailable"
    with pytest.raises(admission.LegacyAdmissionError, match=reason):
        admission.require_guarded_quiescence()
    assert tuple(root.iterdir()) == before


def test_modeled_later_boot_receives_physical_barrier_and_current_writer(
    sealed: tuple[Path, dict[str, object]],
) -> None:
    root, _document = sealed
    before = admission.require_guarded_quiescence()
    with pr_authority.guard(), pr_authority.guard(timeout=0):
        assert (root / "pr-rewrite-authority.lock" / "writer.lock").is_file()
        assert admission.require_guarded_quiescence(expected=before) == before
    with pytest.raises((IsADirectoryError, PermissionError)):
        (root / "pr-rewrite-authority.lock").open("a+b")


@pytest.mark.parametrize("current", [
    SEALED_BOOT, replace(LATER_BOOT, domain="2" * 32),
])
def test_same_boot_or_other_execution_domain_is_not_quiescence(
    sealed: tuple[Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch,
    current: boot.ExecutionDomainBootEvidence,
) -> None:
    monkeypatch.setattr(boot, "native_execution_domain_boot_evidence", lambda: current)
    with pytest.raises(admission.LegacyAdmissionError, match="later boot"):
        admission.require_guarded_quiescence()
    # Ordinary current publication is a different contract from recovery.
    with pr_authority.guard():
        pass


def test_unsupported_boot_provider_refuses_only_recovery(
    sealed: tuple[Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> boot.ExecutionDomainBootEvidence:
        raise boot.ExecutionDomainEvidenceError("unsupported provider")
    monkeypatch.setattr(boot, "native_execution_domain_boot_evidence", unavailable)
    with pytest.raises(admission.LegacyAdmissionError, match="boot evidence is unavailable"):
        admission.require_guarded_quiescence()
    with pr_authority.guard():
        pass


@pytest.mark.parametrize("field,value", [
    ("phase", "prepared"), ("contract", "unknown"), ("authority_contract", "unknown"),
    ("root", [0, 0]), ("directory", [0, 0]), ("original", [0, 0]),
    ("barrier", [True, 1]), ("sealed_boot", {}),
])
def test_label_or_caller_document_cannot_replace_physical_evidence(
    sealed: tuple[Path, dict[str, object]], field: str, value: object,
) -> None:
    root, document = sealed
    rewrite(root, {**document, field: value})
    before = tuple(root.rglob("*"))
    with pytest.raises(admission.LegacyAdmissionError):
        admission.require_guarded_quiescence()
    with pytest.raises(admission.LegacyAdmissionError):
        with pr_authority.guard():
            pytest.fail("unknown transition admitted publication")
    assert tuple(root.rglob("*")) == before


@pytest.mark.parametrize("damage", ["retained", "barrier", "extra", "journal-missing", "duplicate"])
def test_changed_topology_is_retained_and_refused(
    sealed: tuple[Path, dict[str, object]], damage: str,
) -> None:
    root, _document = sealed
    directory = root / "pr-legacy-admission"
    if damage == "retained":
        (directory / "retained.lock").rename(directory / "unknown.lock")
        (directory / "retained.lock").write_bytes(b"0")
    elif damage == "barrier":
        (root / "pr-rewrite-authority.lock").rename(root / "retained-barrier")
        (root / "pr-rewrite-authority.lock").mkdir()
    elif damage == "extra":
        (root / "pr-rewrite-authority.lock" / "unexpected").write_bytes(b"0")
    elif damage == "journal-missing":
        (directory / "transition.json").rename(directory / "retained-journal.json")
    else:
        (directory / "transition.json").write_text('{"phase":"sealed","phase":"sealed"}')
    with pytest.raises(admission.LegacyAdmissionError):
        admission.require_guarded_quiescence()


def test_qualification_rechecks_physical_seal_after_native_observation(
    sealed: tuple[Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _document = sealed
    def changed() -> boot.ExecutionDomainBootEvidence:
        (root / "pr-rewrite-authority.lock").rename(root / "retained-barrier")
        (root / "pr-rewrite-authority.lock").mkdir()
        return LATER_BOOT
    monkeypatch.setattr(boot, "native_execution_domain_boot_evidence", changed)
    with pytest.raises(admission.LegacyAdmissionError):
        admission.require_guarded_quiescence()


def test_retained_file_alias_is_not_a_supported_closed_namespace(
    sealed: tuple[Path, dict[str, object]],
) -> None:
    root, _document = sealed
    os.link(root / "pr-legacy-admission" / "retained.lock", root / "other-entry.lock")
    with pytest.raises(admission.LegacyAdmissionError, match="unaliased"):
        admission.require_guarded_quiescence()


def test_guarded_entry_proof_is_not_complete_recovery_admission(
    sealed: tuple[Path, dict[str, object]],
) -> None:
    assert admission.require_guarded_quiescence()
    with pytest.raises(admission.LegacyAdmissionError, match="foreign-provider"):
        admission.require_quiescence()


@pytest.mark.parametrize("kind", [0, 1, 2, 4, 5, 6])
def test_unknown_or_shared_volume_cannot_use_one_hosts_boot_as_exclusion(
    sealed: tuple[Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch, kind: int,
) -> None:
    monkeypatch.setattr(admission, "_native_volume_type", lambda _root: kind)
    with pytest.raises(admission.LegacyAdmissionError, match="fixed local volume"):
        admission.require_guarded_quiescence()
