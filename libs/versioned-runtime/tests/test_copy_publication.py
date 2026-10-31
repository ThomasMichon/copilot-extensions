"""Created-FD copy sets retain publication custody through actual settlement."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_publication as publication

HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def case(tmp_path: Path):
    root, workspace, sources = tmp_path / "runtime", tmp_path / "evidence", tmp_path / "sources"
    root.mkdir()
    workspace.mkdir(mode=0o700)
    sources.mkdir()
    targets = [root / "resolver", tmp_path / "home/.local/bin/tool", root / "hook"]
    bindings, before = [], {}
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"old-{index}\n".encode())
        source = sources / str(index)
        source.write_bytes(f"new-{index}\n".encode())
        before[target] = target.read_bytes()
        bindings.append(publication.CopyBinding(source, target, hashlib.sha256(source.read_bytes()).hexdigest()))
    return publication.CopyPublication(root, workspace, bindings), before


@pytest.mark.parametrize("settlement", ["keep", "retain", "rollback"])
def test_real_contender_cannot_publish_until_actual_settlement(tmp_path, settlement):
    transaction, before = case(tmp_path)
    transaction.publish()
    target = transaction.bindings[0].target
    entered = tmp_path / "entered"
    script = """
import pathlib,sys
sys.path.insert(0,sys.argv[1])
import versioned_runtime_publication as p
print("ready",flush=True)
with p.target_leases(pathlib.Path(sys.argv[2]),[pathlib.Path(sys.argv[3])]):
    pathlib.Path(sys.argv[4]).write_text("entered")
print("settled",flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", script, str(LIBRARY), str(transaction.root),
         str(target), str(entered)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=HEADLESS,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=0.2)
        assert not entered.exists()
        getattr(transaction, settlement)()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
        assert entered.read_text() == "entered"
        if settlement == "rollback":
            assert {path: path.read_bytes() for path in before} == before
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_retained_settlement_supports_recovery_without_live_publication(tmp_path):
    transaction, before = case(tmp_path)
    transaction.publish()
    context, receipts, expected = transaction.context, transaction.receipts, transaction.expected
    targets = list(before)
    retained = [context, *receipts, *transaction.backups]
    states = {path: publication.state_path(path) for path in retained}
    transaction.retain()
    assert transaction._custody is None
    del transaction
    assert {path: publication.state_path(path) for path in retained} == states
    publication.check_owned(context, receipts, targets, expected)
    publication.recover_owned(
        context, receipts, targets, context.parent / "later-recovery.jsonl", expected,
    )
    assert {path: path.read_bytes() for path in targets} == before


def test_retained_proofs_survive_the_real_producer_process(tmp_path):
    transaction, before = case(tmp_path)
    script = """
import hashlib,json,pathlib,sys
sys.path.insert(0,sys.argv[1])
import versioned_runtime_publication as p
root,workspace,sources=map(pathlib.Path,sys.argv[2:5])
targets=list(map(pathlib.Path,json.loads(sys.argv[5])))
bindings=[p.CopyBinding(sources/str(i),target,
    hashlib.sha256((sources/str(i)).read_bytes()).hexdigest())
    for i,target in enumerate(targets)]
p.CopyPublication(root,workspace,bindings).publish().retain()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(LIBRARY),
         str(transaction.root), str(transaction.workspace),
         str(transaction.bindings[0].source.parent), json.dumps(list(map(str, before)))],
        capture_output=True, text=True, timeout=30, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    publication.check_owned(
        transaction.context, transaction.receipts, list(before), transaction.expected,
    )
    publication.recover_owned(
        transaction.context, transaction.receipts, list(before),
        transaction.workspace / "after-exit.jsonl", transaction.expected,
    )
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("foreign", [False, True])
def test_explicit_prefix_recovery_after_real_producer_death(tmp_path, foreign):
    transaction, before = case(tmp_path)
    baseline = {path: publication.state_path(path) for path in before}
    script = """
import hashlib,json,os,pathlib,sys
sys.path.insert(0,sys.argv[1])
import versioned_runtime_publication as p
root,workspace,sources=map(pathlib.Path,sys.argv[2:5])
targets=list(map(pathlib.Path,json.loads(sys.argv[5])))
bindings=[p.CopyBinding(sources/str(i),target,
    hashlib.sha256((sources/str(i)).read_bytes()).hexdigest())
    for i,target in enumerate(targets)]
original=os.replace
def replace(source,target):
    original(source,target)
    if pathlib.Path(target)==targets[0]:
        os._exit(73)
p.os.replace=replace
p.CopyPublication(root,workspace,bindings).publish()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(LIBRARY),
         str(transaction.root), str(transaction.workspace),
         str(transaction.bindings[0].source.parent), json.dumps(list(map(str, before)))],
        capture_output=True, text=True, timeout=30, creationflags=HEADLESS,
    )
    assert result.returncode == 73, result.stdout + result.stderr
    targets = list(before)
    assert targets[0].read_bytes() != before[targets[0]]
    with pytest.raises(publication.PublicationProofError, match="incomplete"):
        publication.recover_uncontested(
            transaction.context, transaction.receipts, transaction.workspace / "refused.jsonl",
            transaction.expected, baseline,
        )
    assert not (transaction.workspace / "refused.jsonl").exists()
    if foreign:
        peer = targets[0].with_name("peer-replacement")
        peer.write_bytes(targets[0].read_bytes())
        os.replace(peer, targets[0])
    observed = publication.state_path(targets[0])
    refused = publication.recover_uncontested(
        transaction.context, transaction.receipts, transaction.workspace / "explicit.jsonl",
        transaction.expected, baseline, recovery_only=True,
    )
    if foreign:
        assert refused == [targets[0]]
        assert publication.state_path(targets[0]) == observed
    else:
        assert refused == []
        assert {path: path.read_bytes() for path in targets} == before
    assert all(path.exists() for path in [transaction.context, *transaction.receipts])


@pytest.mark.parametrize("damage", ["target", "backup", "context", "receipt"])
def test_retained_settlement_refuses_drift_without_releasing_custody(tmp_path, damage):
    transaction, _before = case(tmp_path)
    transaction.publish()
    path = {
        "target": transaction.bindings[0].target,
        "backup": next(iter(transaction.backups)),
        "context": transaction.context,
        "receipt": transaction.receipts[0],
    }[damage]
    path.write_bytes(b"foreign bytes")
    with pytest.raises(publication.PublicationProofError):
        transaction.retain()
    assert transaction._custody is not None
    assert path.read_bytes() == b"foreign bytes"
    transaction._release()


def test_retained_settlement_requires_a_successful_unsettled_copy(tmp_path):
    transaction, _before = case(tmp_path)
    with pytest.raises(publication.PublicationProofError, match="successful publication"):
        transaction.retain()
    transaction.publish()
    transaction.retain()
    with pytest.raises(publication.PublicationProofError, match="custody is not held"):
        transaction.retain()


@pytest.mark.parametrize("damage", ["inode", "modified", "missing-created"])
def test_retained_settlement_requires_original_created_output_states(tmp_path, damage):
    transaction, _before = case(tmp_path)
    transaction.publish()
    receipt = transaction.receipts[0]
    records = [json.loads(line) for line in receipt.read_text().splitlines()]
    if damage == "missing-created":
        del records[1]
        for sequence, record in enumerate(records[1:-1], 1):
            record["sequence"] = sequence
        records[-1]["sequence"] = len(records) - 2
    else:
        records[1]["state"][damage] += 1
    receipt.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    observed = {path: publication.state_path(path) for path in transaction.outputs}
    with pytest.raises(publication.PublicationProofError, match="absent from its proofs"):
        transaction.retain()
    assert transaction._custody is not None
    assert {path: publication.state_path(path) for path in observed} == observed
    assert receipt.exists() and transaction.context.exists()
    transaction._release()


@pytest.mark.parametrize("settlement", ["retain", "rollback", "keep"])
@pytest.mark.parametrize("damage", ["root", "missing-target", "no-backup", "backup-path", "backup-state"])
def test_valid_context_drift_cannot_change_captured_recovery_bindings(
    tmp_path, capsys, settlement, damage,
):
    transaction, _before = case(tmp_path)
    transaction.publish()
    observed = {item.target: publication.state_path(item.target) for item in transaction.bindings}
    evidence = [transaction.context, *transaction.receipts, *transaction.backups]
    context = json.loads(transaction.context.read_text())
    if damage == "root":
        context["root"] = str(tmp_path)
    elif damage == "missing-target":
        context["entries"].pop()
    elif damage == "no-backup":
        context["entries"][0].update(backup=None, state=None)
    elif damage == "backup-path":
        context["entries"][0]["backup"] = context["entries"][1]["backup"]
        context["entries"][0]["state"] = context["entries"][1]["state"]
    else:
        context["entries"][0]["state"]["modified"] += 1
    transaction.context.write_text(json.dumps(context) + "\n", encoding="utf-8")
    if settlement == "keep":
        transaction.keep()
        assert "captured bindings" in capsys.readouterr().err
    else:
        with pytest.raises(publication.PublicationProofError, match="captured bindings"):
            getattr(transaction, settlement)()
    assert {path: publication.state_path(path) for path in observed} == observed
    assert all(path.exists() for path in evidence)
    if settlement == "retain":
        assert transaction._custody is not None
        transaction._release()


@pytest.mark.parametrize("fault", ["partial-stage", "post-rename", "source-set"])
def test_ordinary_copy_failure_recovers_every_earlier_created_output(tmp_path, monkeypatch, fault):
    transaction, before = case(tmp_path)
    original_stage, original_replace = publication._stage, publication.os.replace
    fired = False

    def stage(root, target, data, mode, receipt, **kwargs):
        if fault in ("partial-stage", "source-set") and target == transaction.bindings[-1].target:
            if fault == "partial-stage" and kwargs.get("phase") == "forward":
                fd, name = tempfile.mkstemp(dir=target.parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"partial")
                raise OSError(f"Injected partial stage: {name}")
            if fault == "source-set" and kwargs.get("phase") == "forward":
                (transaction.bindings[0].source.parent / "unexpected").write_text("changed")
        return original_stage(root, target, data, mode, receipt, **kwargs)

    def replace(source, destination):
        nonlocal fired
        original_replace(source, destination)
        if fault == "post-rename" and not fired and Path(destination) == transaction.bindings[-1].target:
            fired = True
            raise OSError("Injected after the actual rename")

    def validate():
        if (transaction.bindings[0].source.parent / "unexpected").exists():
            raise publication.PublicationProofError("Source file set changed")

    transaction.validate_inputs = validate
    monkeypatch.setattr(publication, "_stage", stage)
    monkeypatch.setattr(publication.os, "replace", replace)
    with pytest.raises((OSError, publication.PublicationProofError), match="Injected|file set"):
        transaction.publish()
    assert {path: path.read_bytes() for path in before} == before
    assert transaction._custody is None


@pytest.mark.parametrize("foreign", ["directory", "same-bytes", "modified"])
def test_foreign_final_target_is_preserved_while_earlier_owned_outputs_recover(
    tmp_path, monkeypatch, foreign,
):
    transaction, before = case(tmp_path)
    target = transaction.bindings[-1].target
    original = publication._stage
    peer = None

    def stage(root, destination, data, mode, receipt, **kwargs):
        nonlocal peer
        result = original(root, destination, data, mode, receipt, **kwargs)
        if destination == target and kwargs.get("phase") == "forward":
            if foreign == "directory":
                target.unlink()
                target.mkdir()
                (target / "sentinel").write_text("peer")
            else:
                replacement = target.with_name("peer")
                replacement.write_bytes(before[target] if foreign == "same-bytes" else b"peer")
                os.replace(replacement, target)
            peer = target.stat().st_ino
            raise OSError("Injected peer replacement before rename")
        return result

    monkeypatch.setattr(publication, "_stage", stage)
    with pytest.raises(publication.PublicationProofError, match="recovery incomplete"):
        transaction.publish()
    for path in before:
        if path != target:
            assert path.read_bytes() == before[path]
    assert target.stat().st_ino == peer
    if foreign == "directory":
        assert [path.name for path in target.iterdir()] == ["sentinel"]
    else:
        assert target.read_bytes() == (before[target] if foreign == "same-bytes" else b"peer")
    assert transaction.context.exists() and transaction.receipts[0].exists()


@pytest.mark.parametrize("settlement", ["keep", "retain"])
def test_noop_is_explicit_but_never_a_created_ownership_claim(tmp_path, settlement):
    transaction, _before = case(tmp_path)
    item = transaction.bindings[0]
    item.target.write_bytes(item.source.read_bytes().replace(b"\n", b"\r\n"))
    before = publication.state_path(item.target)
    assert before.digest == hashlib.sha256(item.target.read_bytes()).hexdigest(), (
        "Native physical state must hash literal CRLF bytes, not translated descriptor text"
    )
    transaction.bindings[0] = publication.CopyBinding(
        item.source, item.target, item.digest, normalized_noop=True,
    )
    transaction.publish()
    assert publication.state_path(item.target) == before
    records = [json.loads(line) for line in transaction.receipts[0].read_text().splitlines()]
    assert any(row.get("kind") == "noop" for row in records)
    owned = publication.read_receipt(transaction.receipts[0], transaction.root, set(transaction.baseline))
    assert owned[item.target] == []
    getattr(transaction, settlement)()


def test_foreign_earlier_output_survives_a_later_copy_failure(tmp_path, monkeypatch):
    transaction, before = case(tmp_path)
    first, last = transaction.bindings[0].target, transaction.bindings[-1].target
    original = publication._stage
    peer_state = None

    def stage(root, target, data, mode, receipt, **kwargs):
        nonlocal peer_state
        if target == last and kwargs.get("phase") == "forward":
            peer = first.with_name("peer")
            peer.write_bytes(b"foreign earlier helper")
            os.replace(peer, first)
            peer_state = publication.state_path(first)
            raise OSError("Injected later stage failure")
        return original(root, target, data, mode, receipt, **kwargs)

    monkeypatch.setattr(publication, "_stage", stage)
    with pytest.raises(publication.PublicationProofError, match="recovery incomplete"):
        transaction.publish()
    assert publication.state_path(first) == peer_state
    assert first.read_bytes() == b"foreign earlier helper"
    assert {path: path.read_bytes() for path in before if path != first} == {
        path: data for path, data in before.items() if path != first
    }
    assert transaction.context.exists() and transaction.receipts[0].exists()


@pytest.mark.skipif(os.name != "nt", reason="native CRT descriptor translation")
def test_created_fd_capture_preserves_position_and_callers_text_mode(tmp_path):
    path = tmp_path / "literal"
    path.write_bytes(b"a\r\nb\r\n")
    fd = os.open(path, os.O_RDONLY | os.O_TEXT)
    try:
        value = publication.state_fd(fd)
        assert value.digest == hashlib.sha256(path.read_bytes()).hexdigest()
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        assert os.read(fd, 20) == b"a\nb\n"
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name != "nt", reason="native source owner differs from its copying principal")
def test_declared_content_read_does_not_confer_created_object_ownership(tmp_path, monkeypatch):
    transaction, _before = case(tmp_path)
    source_inodes = {item.source.stat().st_ino for item in transaction.bindings}
    original = publication._windows_owned
    monkeypatch.setattr(publication, "_windows_owned", lambda fd: (
        os.fstat(fd).st_ino not in source_inodes and original(fd)
    ))
    with pytest.raises(publication.PublicationProofError, match="not owned"):
        publication.state_path(transaction.bindings[0].source)
    transaction.publish()
    assert all(item.target.read_bytes() == item.source.read_bytes() for item in transaction.bindings)
    transaction.keep()


def test_corrupt_backup_keeps_every_proof_and_backup_after_live_commit(tmp_path, capsys):
    transaction, _before = case(tmp_path)
    transaction.publish()
    backups = list(transaction.backups)
    backups[-1].write_bytes(b"corrupt")
    transaction.keep()
    assert transaction.context.exists()
    assert all(path.exists() for path in [*transaction.receipts, *backups])
    assert "Backup changed" in capsys.readouterr().err
    assert transaction._custody is None


def test_foreign_poststate_is_not_overwritten_at_live_keep(tmp_path, capsys):
    transaction, _before = case(tmp_path)
    transaction.publish()
    target = transaction.bindings[0].target
    peer = target.with_name("peer")
    peer.write_bytes(b"peer")
    os.replace(peer, target)
    identity = target.stat().st_ino
    transaction.keep()
    assert target.read_bytes() == b"peer" and target.stat().st_ino == identity
    assert transaction.context.exists() and transaction.receipts[0].exists()
    assert "Copy target changed" in capsys.readouterr().err


@pytest.mark.parametrize("fault", ["missing-proof", "corrupt-proof", "corrupt-backup"])
def test_uncertain_recovery_keeps_full_set_and_evidence(tmp_path, fault):
    transaction, _before = case(tmp_path)
    transaction.publish()
    observed = {item.target: publication.state_path(item.target) for item in transaction.bindings}
    if fault == "missing-proof":
        transaction.receipts[0].unlink()
    elif fault == "corrupt-proof":
        transaction.receipts[0].write_bytes(b"corrupt\n")
    else:
        next(iter(transaction.backups)).write_bytes(b"corrupt")
    with pytest.raises(publication.PublicationProofError):
        transaction.rollback()
    assert {path: publication.state_path(path) for path in observed} == observed
    assert transaction.context.exists() and all(path.exists() for path in transaction.backups)
    assert transaction._custody is None


def test_strict_restore_cli_still_completes_an_error_receipt_for_bad_required_proof(tmp_path):
    transaction, _before = case(tmp_path)
    transaction.publish()
    transaction.rollback()
    transaction.receipts[0].write_bytes(b"corrupt\n")
    output = transaction.workspace / "strict-error.jsonl"
    argv = [
        sys.executable, "-I", "-B", str(LIBRARY / "versioned_runtime.py"),
        "--root", str(transaction.root), "publication-restore",
        "--context", str(transaction.context), "--output-receipt", str(output),
        "--path", str(transaction.bindings[0].target),
    ]
    for receipt in transaction.receipts:
        argv.extend(["--receipt", str(receipt)])
    for target, digest in transaction.expected:
        argv.extend(["--expected", target, digest])
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10, creationflags=HEADLESS)
    assert result.returncode == 1 and result.stdout == ""
    assert json.loads(output.read_text().splitlines()[-1])["outcome"] == "error"


@pytest.mark.parametrize("fault", ["duplicate", "unsafe-target", "changed-source"])
def test_copy_binding_admission_refuses_before_any_write(tmp_path, fault):
    transaction, before = case(tmp_path)
    if fault == "duplicate":
        transaction.bindings.append(transaction.bindings[0])
        with pytest.raises(publication.PublicationProofError, match="duplicated"):
            publication.CopyPublication(transaction.root, transaction.workspace, transaction.bindings)
    else:
        target = transaction.bindings[-1].target
        if fault == "unsafe-target":
            target.unlink()
            target.mkdir()
        else:
            transaction.bindings[-1].source.write_text("changed")
        with pytest.raises(publication.PublicationProofError):
            transaction.publish()
        for path in before:
            if path != target:
                assert path.read_bytes() == before[path]
