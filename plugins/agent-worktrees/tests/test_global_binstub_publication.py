"""The required global tools publish exact files before runtime activation."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import os
import tempfile

import pytest

from test_source_python_publication import (
    CALLBACK_TIMEOUT, admitted, native_hosts, publication_case as shared_publication_case,
)

CRITICAL = "agent-worktrees.cmd"
NAMES = ("agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1")
publication_case = shared_publication_case


@pytest.fixture
def publication_target():
    return CRITICAL


def publication_io_transform(fault, shell, monkeypatch, publication_target):
    return lambda _content: None


def inject_copy_fault(driver, fault, critical, monkeypatch):
    publication = driver.runtime._runtime_component("versioned_runtime_publication")
    original_stage, original_replace = publication._stage, publication.os.replace
    fired = False

    def stage(root, target, data, mode, receipt, **kwargs):
        if fault == "partial-write" and target.name == critical and kwargs.get("phase") == "forward":
            fd, name = tempfile.mkstemp(prefix=f".{critical}.partial-", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"partial")
            raise OSError(f"Injected partial file stage for {critical}: {name}")
        return original_stage(root, target, data, mode, receipt, **kwargs)

    def replace(source, target):
        nonlocal fired
        if fault == "directory-race" and Path(target).name == critical and not fired:
            fired = True
            target = Path(target)
            target.unlink()
            target.mkdir()
            (target / "sentinel").write_text("peer")
        return original_replace(source, target)

    monkeypatch.setattr(publication, "_stage", stage)
    monkeypatch.setattr(publication.os, "replace", replace)


@pytest.fixture
def publication_transform(fault, shell, monkeypatch, publication_target):
    return publication_io_transform(fault, shell, monkeypatch, publication_target)


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("fault", ["healthy", "same-content", "initial-directory", "directory-race", "partial-write"])
@pytest.mark.timeout(90)
def test_actual_callback_global_file_publication(
    publication_case, shell, executable, fault, monkeypatch,
):
    driver, root, slot, owner, content, _identity = publication_case
    local_bin = root.parent / ".local" / "bin"
    local_bin.mkdir(parents=True)
    for name in NAMES:
        value = (content / "bin" / name).read_bytes() if fault == "same-content" else b"previous\n"
        if fault == "same-content":
            value = value.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        (local_bin / name).write_bytes(value)
    resolvers = [root / "bin" / name for name in ("resolve-runtime.ps1", "resolve-runtime.sh")]
    for resolver in resolvers:
        resolver.parent.mkdir(parents=True, exist_ok=True)
        resolver.write_bytes(b"previous resolver\n")
    target = local_bin / CRITICAL
    inject_copy_fault(driver, fault, CRITICAL, monkeypatch)
    if fault == "initial-directory":
        target.unlink()
        target.mkdir()
        (target / "sentinel").write_text("peer", encoding="utf-8")
    with admitted(driver, root, owner, content, monkeypatch) as (governance, _lease):
        for name in ("current-version", "last-known-good"):
            (root / name).write_text("previous\n", encoding="utf-8")
        protected = {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in (root / "current-version", root / "last-known-good", root / "deploy-manifest.json")
        }
        before = {
            name: ((local_bin / name).read_bytes(), (local_bin / name).stat().st_ino,
                   (local_bin / name).stat().st_mtime_ns)
            for name in NAMES if (local_bin / name).is_file()
        }
        with driver.runtime.retention_custody(root, slot.name, payload_hash="candidate") as (_slot, descriptor):
            if fault in ("healthy", "same-content"):
                transaction = driver._publish_helpers(
                    governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                    content, slot, descriptor, timeout=CALLBACK_TIMEOUT, base_python=Path(sys._base_executable),
                )
                for name in NAMES:
                    if fault == "same-content" and shell == "ps1":
                        current = local_bin / name
                        assert (current.read_bytes(), current.stat().st_ino, current.stat().st_mtime_ns) == before[name]
                    else:
                        assert (local_bin / name).read_bytes() == (content / "bin" / name).read_bytes()
                assert not list(local_bin.glob(".*.publication.*"))
                transaction.keep()
            else:
                with pytest.raises((OSError, ValueError, subprocess.CalledProcessError)) as caught:
                    driver._publish_helpers(
                        governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                        content, slot, descriptor, timeout=CALLBACK_TIMEOUT, base_python=Path(sys._base_executable),
                    )
                diagnostic = (str(caught.value) + str(getattr(caught.value, "stderr", ""))).casefold()
                assert CRITICAL in diagnostic, diagnostic
                if fault == "partial-write":
                    assert target.read_bytes() == b"previous\n"
                    assert any(path.read_bytes() == b"partial" for path in local_bin.rglob("*") if path.is_file())
                else:
                    assert target.is_dir()
                    assert [path.name for path in target.iterdir()] == ["sentinel"]
                    assert (target / "sentinel").read_text() == "peer"
                for name in NAMES:
                    if name != CRITICAL:
                        assert (local_bin / name).read_bytes() == before[name][0], (
                            f"Earlier uncontested global binstub changed after {CRITICAL} failed: {name}"
                        )
                assert all(path.read_bytes() == b"previous resolver\n" for path in resolvers)
        assert {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in protected
        } == protected
