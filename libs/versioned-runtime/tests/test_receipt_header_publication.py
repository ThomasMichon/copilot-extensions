"""A receipt name never exposes an incomplete producer header."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_publication as proof

FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@pytest.mark.parametrize("boundary", ["empty", "partial", "synced"])
def test_interrupted_header_is_unpublished_and_fresh_attempt_preserves_evidence(
    tmp_path, monkeypatch, boundary,
):
    path, target = tmp_path / "producer.jsonl", tmp_path / "target"
    target.write_bytes(b"unchanged target\n")
    original = proof.Receipt._append

    def interrupt(receipt, value):
        assert value["kind"] == "header"
        if boundary == "partial":
            receipt.stream.write(b'{"unfinished":')
            receipt.stream.flush()
            os.fsync(receipt.stream.fileno())
        elif boundary == "synced":
            original(receipt, value)
        raise OSError("injected header interruption")

    with monkeypatch.context() as patch:
        patch.setattr(proof.Receipt, "_append", interrupt)
        with pytest.raises(OSError, match="injected header interruption"):
            proof.Receipt(path, tmp_path, [target], "files")
    assert not path.exists()
    attempts = [item for item in tmp_path.iterdir() if item != target]
    assert len(attempts) == 1
    retained = {item: (item.read_bytes(), item.stat().st_ino) for item in attempts}
    receipt = proof.Receipt(path, tmp_path, [target], "files")
    receipt.finish("success")
    assert proof.read_receipt(path, tmp_path, {target}) == {target: []}
    assert {item: (item.read_bytes(), item.stat().st_ino) for item in attempts} == retained
    assert target.read_bytes() == b"unchanged target\n"


def test_header_preserves_single_link_producer_identity_and_strict_reader(tmp_path):
    path, target = tmp_path / "producer.jsonl", tmp_path / "target"
    receipt = proof.Receipt(path, tmp_path, [target], "files")
    try:
        header = json.loads(path.read_bytes())
        captured = proof.state_fd(receipt.stream.fileno())
        assert path.stat().st_nlink == 1
        assert header["file_identity"] == [captured.device, captured.inode]
        assert proof.read_receipt(path, tmp_path, {target}, recovery_only=True) == {target: []}
        with pytest.raises(proof.PublicationProofError, match="complete producer outcome"):
            proof.read_receipt(path, tmp_path, {target})
    finally:
        receipt.finish("success")


@pytest.mark.parametrize("foreign", ["file", "directory"])
def test_initial_receipt_collision_never_replaces_foreign_name(tmp_path, foreign):
    path, target = tmp_path / "producer.jsonl", tmp_path / "target"
    if foreign == "file":
        path.write_bytes(b"foreign receipt\n")
    else:
        path.mkdir()
    before = path.stat().st_ino
    with pytest.raises(OSError):
        proof.Receipt(path, tmp_path, [target], "files")
    assert path.stat().st_ino == before
    assert path.read_bytes() == b"foreign receipt\n" if foreign == "file" else path.is_dir()
    assert not target.exists()


def test_atomic_publication_preserves_a_concurrent_foreign_winner(tmp_path, monkeypatch):
    path, target = tmp_path / "producer.jsonl", tmp_path / "target"
    original = proof._publish_receipt
    foreign = None

    def collide(source, destination):
        nonlocal foreign
        destination.write_bytes(b"foreign concurrent receipt\n")
        foreign = destination.stat().st_ino
        original(source, destination)

    monkeypatch.setattr(proof, "_publish_receipt", collide)
    with pytest.raises(OSError):
        proof.Receipt(path, tmp_path, [target], "files")
    assert path.read_bytes() == b"foreign concurrent receipt\n" and path.stat().st_ino == foreign
    attempts = [item for item in tmp_path.iterdir() if item != path]
    assert len(attempts) == 1 and attempts[0].stat().st_nlink == 1
    assert json.loads(attempts[0].read_bytes())["kind"] == "header"
    assert not target.exists()


def test_reopen_rejects_a_replaced_receipt_without_appending_to_it(tmp_path, monkeypatch):
    path, target = tmp_path / "producer.jsonl", tmp_path / "target"
    original = proof._publish_receipt
    foreign = None

    def replace(source, destination):
        nonlocal foreign
        original(source, destination)
        peer = tmp_path / "foreign"
        peer.write_bytes(b"foreign replacement\n")
        foreign = peer.stat().st_ino
        os.replace(peer, destination)

    monkeypatch.setattr(proof, "_publish_receipt", replace)
    with pytest.raises(proof.PublicationProofError, match="append custody"):
        proof.Receipt(path, tmp_path, [target], "files")
    assert path.read_bytes() == b"foreign replacement\n" and path.stat().st_ino == foreign
    assert not target.exists()


@pytest.mark.parametrize("kind", ["copy", "selectors"])
@pytest.mark.parametrize("refuse", [False, True])
def test_before_effects_observes_complete_header_and_unchanged_targets(tmp_path, kind, refuse):
    target = tmp_path / ("command" if kind == "copy" else "current-version")
    target.write_bytes(b"previous\n")
    target.chmod(0o600)
    before = proof.state_path(target)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    path = workspace / ("copy-forward.jsonl" if kind == "copy" else "producer.jsonl")
    targets = {target} if kind == "copy" else {target, tmp_path / "last-known-good"}
    called = []

    def before_effects():
        assert proof.read_receipt(path, tmp_path, targets, recovery_only=True) == {
            item: [] for item in targets
        }
        assert proof.state_path(target) == before
        if kind == "copy":
            assert proof.read_context(workspace / "copy-context.json")[0] == tmp_path
        called.append(True)
        if refuse:
            raise OSError("injected before-effects refusal")

    def invoke():
        if kind == "copy":
            source = workspace / "source"
            source.write_bytes(b"new\n")
            return proof.CopyPublication(
                tmp_path, workspace,
                [proof.CopyBinding(source, target, proof.state_path(source).digest)],
                before_effects=before_effects,
            ).publish()
        return proof.publish_selectors(
            tmp_path, "new", path, None, before_effects=before_effects,
        )

    if refuse:
        with pytest.raises(OSError, match="before-effects refusal"):
            invoke()
        assert proof.state_path(target) == before
    else:
        result = invoke()
        assert target.read_bytes() == b"new\n"
        assert proof.state_path(target) in proof.read_receipt(path, tmp_path, targets)[target]
        if result is not None:
            result.keep()
    assert called == [True]


@pytest.mark.parametrize("boundary", ["empty", "partial", "synced"])
@pytest.mark.timeout(30)
def test_abrupt_header_producer_leaves_no_published_name(tmp_path, boundary):
    path = tmp_path / "producer.jsonl"
    program = """
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import versioned_runtime_publication as proof
path=Path(sys.argv[2])
boundary=sys.argv[3]
original=proof.Receipt._append
if boundary!="retry":
    def stop(receipt,value):
        if boundary=="partial":
            receipt.stream.write(b'{"unfinished":')
            receipt.stream.flush()
            os.fsync(receipt.stream.fileno())
        elif boundary=="synced":
            original(receipt,value)
        os._exit(47)
    proof.Receipt._append=stop
receipt=proof.Receipt(path,path.parent,[path.parent/"target"],"files")
receipt.finish("success")
print(json.dumps(proof.read_receipt(path,path.parent,{path.parent/"target"})[path.parent/"target"]))
"""
    failed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(LIBRARY), str(path), boundary],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **FLAGS,
    )
    assert failed.returncode == 47, failed.stderr
    assert not path.exists()
    attempts = {item: (item.read_bytes(), item.stat().st_ino) for item in tmp_path.iterdir()}
    assert len(attempts) == 1
    retry = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(LIBRARY), str(path), "retry"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **FLAGS,
    )
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout) == []
    assert {item: (item.read_bytes(), item.stat().st_ino) for item in attempts} == attempts
