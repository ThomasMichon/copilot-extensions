"""POSIX input-file controls; no real key, database, service or process."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX input custody contract")
SOURCE = Path(__file__).resolve().parents[1] / "src/agent_vault"


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    package_name = "attachment_file_source_test"
    package = ModuleType(package_name)
    package.__dict__["__path__"] = [str(SOURCE)]
    monkeypatch.setitem(sys.modules, package_name, package)
    loaded: list[ModuleType] = []
    for leaf in ("attachment_import", "attachment_import_cli"):
        name = package_name + "." + leaf
        spec = importlib.util.spec_from_file_location(name, SOURCE / (leaf + ".py"))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return SimpleNamespace(core=loaded[0], client=loaded[1])


def synthetic_file(path: Path, mode: int = 0o600) -> Path:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(b"synthetic attachment, not a cryptographic key")
    return path


def test_existing_owner_private_input_and_group_readable_public_are_accepted(
    source: SimpleNamespace,
    tmp_path: Path,
) -> None:
    private = synthetic_file(tmp_path / "example.key")
    public = synthetic_file(tmp_path / "example.key.pub", 0o640)
    assert source.client._read_file(private, private=True) == private.read_bytes()
    assert source.client._read_file(public, private=False) == public.read_bytes()
    assert private.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "kind", ["exposed", "symlink", "hardlink", "fifo", "parent-link", "empty", "large"]
)
def test_unsafe_attachment_input_refuses_without_changing_original(
    source: SimpleNamespace,
    tmp_path: Path,
    kind: str,
) -> None:
    original = synthetic_file(tmp_path / "original")
    selected = original
    before = original.read_bytes()
    if kind == "exposed":
        original.chmod(0o644)
    elif kind == "symlink":
        selected = tmp_path / "indirect"
        selected.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, tmp_path / "alias")
    elif kind == "fifo":
        selected = tmp_path / "fifo"
        os.mkfifo(selected, 0o600)
    elif kind == "parent-link":
        parent = tmp_path / "indirect"
        parent.symlink_to(tmp_path, target_is_directory=True)
        selected = parent / original.name
    elif kind == "empty":
        original.write_bytes(b"")
        before = b""
    elif kind == "large":
        original.write_bytes(b"x" * (source.core.MAX_ATTACHMENT_BYTES + 1))
        before = original.read_bytes()
    with pytest.raises(source.core.AttachmentImportError):
        source.client._read_file(selected, private=True)
    assert original.read_bytes() == before


def test_input_mode_change_during_read_is_not_accepted(
    source: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = synthetic_file(tmp_path / "example.key")
    fdopen = source.client.os.fdopen

    def changed(descriptor: int, mode: str) -> object:
        original.chmod(0o644)
        return fdopen(descriptor, mode)

    monkeypatch.setattr(source.client.os, "fdopen", changed)
    with pytest.raises(source.core.AttachmentImportError, match="input_custody_invalid"):
        source.client._read_file(original, private=True)
