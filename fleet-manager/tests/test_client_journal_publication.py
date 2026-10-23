"""Client metadata retries retain abandoned attempts without adopting them."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager import client_construction as construction

FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@pytest.mark.parametrize("content", [b"", b'{"unfinished":', b'{"complete":true}'])
def test_legacy_temporary_never_blocks_or_supplies_a_new_journal(tmp_path, content):
    path = tmp_path / "pending.json"
    abandoned = path.with_name(path.name + ".tmp")
    abandoned.write_bytes(content)
    before = abandoned.stat()
    construction._write(path, {"attempt": "new"})
    assert construction._read(path) == {"attempt": "new"}
    assert abandoned.read_bytes() == content
    assert abandoned.stat().st_ino == before.st_ino


@pytest.mark.parametrize("boundary", ["empty", "partial", "complete"])
def test_failed_attempt_preserves_old_journal_and_does_not_poison_retry(tmp_path, monkeypatch, boundary):
    path = tmp_path / "pending.json"
    construction._write(path, {"attempt": "old"})
    before = path.read_bytes(), path.stat().st_ino
    opened = set()
    actual_open, actual_write, actual_replace = os.open, os.write, os.replace

    def observe_open(name, *args, **kwargs):
        fd = actual_open(name, *args, **kwargs)
        if Path(name).name.startswith("." + path.name + ".write-"):
            opened.add(fd)
        return fd

    def interrupt_write(fd, content):
        if fd in opened and boundary != "complete":
            if boundary == "partial":
                actual_write(fd, content[:7])
            raise OSError("injected journal interruption")
        return actual_write(fd, content)

    def interrupt_replace(old, new, *args, **kwargs):
        if Path(new) == path:
            raise OSError("injected journal interruption")
        return actual_replace(old, new, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", observe_open)
        patch.setattr(os, "write", interrupt_write)
        patch.setattr(os, "replace", interrupt_replace)
        with pytest.raises(OSError, match="injected journal interruption"):
            construction._write(path, {"attempt": "interrupted"})
    assert (path.read_bytes(), path.stat().st_ino) == before
    attempts = list(tmp_path.glob("." + path.name + ".write-*"))
    assert len(attempts) == 1
    retained = {item: (item.read_bytes(), item.stat().st_ino) for item in attempts}
    assert len(attempts[0].read_bytes()) == (
        0 if boundary == "empty" else 7 if boundary == "partial"
        else len(construction.canonical({"attempt": "interrupted"}))
    )
    construction._write(path, {"attempt": "retried"})
    assert construction._read(path) == {"attempt": "retried"}
    assert {item: (item.read_bytes(), item.stat().st_ino) for item in attempts} == retained
    assert set(tmp_path.iterdir()) == {path, *attempts}


@pytest.mark.parametrize("progress", [0, 3])
def test_short_writes_are_completed_or_explicitly_refused(tmp_path, monkeypatch, progress):
    path = tmp_path / "pending.json"
    actual_write = os.write

    def short_write(fd, content):
        return actual_write(fd, content[:progress]) if progress else 0

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", short_write)
        if progress:
            construction._write(path, {"attempt": "short-write"})
        else:
            with pytest.raises(OSError, match="no progress"):
                construction._write(path, {"attempt": "short-write"})
    if progress:
        assert construction._read(path) == {"attempt": "short-write"}
    else:
        assert not path.exists()
        assert len(list(tmp_path.glob(".pending.json.write-*"))) == 1
        construction._write(path, {"attempt": "retry"})
        assert construction._read(path) == {"attempt": "retry"}


def test_attempt_name_collision_preserves_the_unowned_file(tmp_path, monkeypatch):
    path = tmp_path / "pending.json"
    attempt = tmp_path / ".pending.json.write-collision"
    attempt.write_bytes(b"foreign evidence")
    before = attempt.stat().st_ino
    with monkeypatch.context() as patch:
        patch.setattr(construction.uuid, "uuid4", lambda: type("Attempt", (), {"hex": "collision"})())
        with pytest.raises(FileExistsError):
            construction._write(path, {"attempt": "collision"})
    assert not path.exists()
    construction._write(path, {"attempt": "retry"})
    assert construction._read(path) == {"attempt": "retry"}
    assert attempt.read_bytes() == b"foreign evidence" and attempt.stat().st_ino == before


@pytest.mark.parametrize("boundary", ["file-sync", "rename", "directory-sync"])
def test_failed_durability_barrier_is_explicit_and_retryable(tmp_path, monkeypatch, boundary):
    path = tmp_path / "pending.json"
    construction._write(path, {"attempt": "old"})

    def interrupt(*args, **kwargs):
        raise OSError("injected durability barrier interruption")

    with monkeypatch.context() as patch:
        if boundary == "file-sync":
            patch.setattr(os, "fsync", interrupt)
        elif boundary == "rename":
            patch.setattr(os, "replace", interrupt)
        else:
            patch.setattr(construction.client_admission, "_sync_directory", interrupt)
        with pytest.raises(OSError, match="injected durability barrier interruption"):
            construction._write(path, {"attempt": "new"})
    assert construction._read(path) == {"attempt": "new" if boundary == "directory-sync" else "old"}
    construction._write(path, {"attempt": "retry"})
    assert construction._read(path) == {"attempt": "retry"}


def test_staged_namespace_does_not_adopt_attempt_named_foreign_state(tmp_path):
    temporary = tmp_path / ("." + construction.NAMESPACE + "-initializing")
    temporary.mkdir(mode=0o700)
    foreign = temporary / ("." + construction.OWNER + ".write-abandoned")
    foreign.write_bytes(b"foreign initialization")
    before = foreign.stat().st_ino
    with pytest.raises(construction.PlanError, match="foreign state"):
        construction._namespace(tmp_path, {"installation": "fixture"})
    assert foreign.read_bytes() == b"foreign initialization" and foreign.stat().st_ino == before
    assert not (tmp_path / construction.NAMESPACE).exists()


@pytest.mark.parametrize("content", [b"", b'{"unfinished":', b'{"complete":true}'])
def test_construction_intent_preserves_legacy_unpublished_evidence(tmp_path, content):
    namespace = construction._namespace(tmp_path, {"installation": "fixture"})
    inputs = namespace / "client-inputs"
    inputs.mkdir(mode=0o700)
    abandoned = inputs / "candidate.json.tmp"
    abandoned.write_bytes(content)
    before = abandoned.stat().st_ino
    intent = construction._construction_intent(
        namespace, "candidate", namespace / "versions" / "candidate", {"input": "new"},
    )
    assert construction._read(intent) == {"input": "new"}
    assert abandoned.read_bytes() == content and abandoned.stat().st_ino == before


@pytest.mark.parametrize("boundary", ["empty", "partial", "complete"])
@pytest.mark.timeout(30)
def test_abrupt_installed_writer_is_retried_by_a_fresh_process(tmp_path, boundary):
    path = tmp_path / "pending.json"
    program = """
import json,os,sys
from pathlib import Path
from fleet_manager import client_construction as construction
path=Path(sys.argv[1])
boundary=sys.argv[2]
if boundary!="retry":
    actual_open,actual_write,actual_replace=os.open,os.write,os.replace
    opened=set()
    def observe_open(name,*args,**kwargs):
        fd=actual_open(name,*args,**kwargs)
        if Path(name).name.startswith("."+path.name+".write-"):
            opened.add(fd)
        return fd
    def stop_write(fd,content):
        if fd in opened and boundary!="complete":
            if boundary=="partial":
                actual_write(fd,content[:7])
            os._exit(47)
        return actual_write(fd,content)
    def stop_replace(old,new,*args,**kwargs):
        if Path(new)==path:
            os._exit(47)
        return actual_replace(old,new,*args,**kwargs)
    os.open,os.write,os.replace=observe_open,stop_write,stop_replace
construction._write(path,{"attempt":boundary})
print(json.dumps(construction._read(path)))
"""
    failed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(path), boundary],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **FLAGS,
    )
    assert failed.returncode == 47, failed.stderr
    assert not path.exists()
    attempts = list(tmp_path.glob(".pending.json.write-*"))
    assert len(attempts) == 1
    retained = attempts[0].read_bytes(), attempts[0].stat().st_ino
    retry = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(path), "retry"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **FLAGS,
    )
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout) == {"attempt": "retry"}
    assert (attempts[0].read_bytes(), attempts[0].stat().st_ino) == retained
    assert set(tmp_path.iterdir()) == {path, *attempts}


@pytest.mark.parametrize("boundary", ["empty", "partial", "complete"])
@pytest.mark.timeout(30)
def test_abrupt_staged_namespace_writer_is_retried_without_new_recovery_names(tmp_path, boundary):
    program = """
import json,os,sys
from pathlib import Path
from fleet_manager import client_construction as construction
root=Path(sys.argv[1])
boundary=sys.argv[2]
if boundary!="retry":
    actual_write=os.write
    def stop_write(fd,content):
        if boundary=="partial":
            actual_write(fd,content[:7])
        if boundary!="complete":
            os._exit(47)
        return actual_write(fd,content)
    def stop_rename(*args,**kwargs):
        os._exit(47)
    os.write,os.rename=stop_write,stop_rename
namespace=construction._namespace(root,{"installation":"fixture"})
print(json.dumps(construction._read(namespace/construction.OWNER)))
"""
    for attempt in (boundary, "retry"):
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program, str(tmp_path), attempt],
            cwd=tmp_path, capture_output=True, text=True, timeout=10, **FLAGS,
        )
        if attempt != "retry":
            assert result.returncode == 47, result.stderr
            assert not (tmp_path / construction.NAMESPACE).exists()
            temporary = tmp_path / ("." + construction.NAMESPACE + "-initializing")
            assert {path.name for path in temporary.iterdir()} == {construction.OWNER}
        else:
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["identity"] == {"installation": "fixture"}
            assert {path.name for path in tmp_path.iterdir()} == {construction.NAMESPACE}
