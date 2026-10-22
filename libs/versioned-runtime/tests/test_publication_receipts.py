from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
REPOSITORY = LIBRARY.parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime
import versioned_runtime_publication as proof
import versioned_runtime_source as source

HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def case(tmp_path: Path, names: tuple[str, ...]) -> tuple[Path, Path, Path, list[list[str]]]:
    root, evidence, payload = tmp_path / "runtime", tmp_path / "evidence", tmp_path / "payload"
    root.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    payload.mkdir()
    bindings = []
    for index, name in enumerate(names):
        value = f"previous-{name}\n".encode()
        (root / name).write_bytes(value)
        (root / name).chmod(0o600)
        backup = evidence / f"before.{index}"
        backup.write_bytes(value)
        backup.chmod(0o600)
        bindings.append([str(root / name), str(backup), hashlib.sha256(value).hexdigest()])
    context = evidence / "context.json"
    proof.create_context(context, root, bindings)
    return root, context, payload, bindings


def expected(root: Path, payload: Path) -> list[list[str]]:
    return [
        [str(root / "payload-dir"), hashlib.sha256((str(payload.resolve()) + "\n").encode()).hexdigest()],
        [str(root / "stamped-version"), hashlib.sha256(b"2.0.0\n").hexdigest()],
    ]


def publish(root: Path, payload: Path, receipt: Path) -> None:
    source.publish_runtime_redirects(root, payload, "2.0.0", ownership_receipt=receipt)


def test_created_stage_receipt_and_native_recovery(tmp_path: Path) -> None:
    root, context, payload, bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    wanted = expected(root, payload)
    targets = [root / "payload-dir", root / "stamped-version"]
    proof.check_owned(context, [receipt], targets, wanted)
    restored = context.parent / "recovery.jsonl"
    proof.recover_owned(context, [receipt], targets, restored, wanted)
    proof.check_owned(context, [receipt, restored], targets, wanted)
    assert {path.name: path.read_bytes() for path in targets} == {
        Path(target).name: Path(backup).read_bytes() for target, backup, _digest in bindings
    }
    proof.cleanup_proofs(context, [receipt, restored], wanted)
    assert not context.exists() and not receipt.exists() and not restored.exists()


def test_same_bytes_peer_after_success_is_not_owned(tmp_path: Path) -> None:
    root, context, payload, _bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    target = root / "payload-dir"
    producer = target.stat()
    replacement = root / "peer"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)
    peer = target.stat()
    assert (producer.st_dev, producer.st_ino) != (peer.st_dev, peer.st_ino)
    before = {path: path.read_bytes() for path in (target, root / "stamped-version")}
    with pytest.raises(proof.PublicationProofError, match="producer-created"):
        proof.check_owned(context, [receipt], [target], expected(root, payload))
    with pytest.raises(proof.PublicationProofError, match="Foreign"):
        proof.recover_owned(context, [receipt], list(before), context.parent / "failed.jsonl", expected(root, payload))
    assert {path: path.read_bytes() for path in before} == before
    assert target.stat().st_ino == peer.st_ino


@pytest.mark.parametrize("fault", [
    "missing", "truncated", "schema", "root", "target", "same-byte-replacement",
    "directory", "symlink", "public-mode",
])
def test_invalid_required_proof_refuses_whole_set(tmp_path: Path, fault: str) -> None:
    if os.name == "nt" and fault in ("symlink", "public-mode"):
        pytest.skip("POSIX permission/link mutation without elevation")
    root, context, payload, _bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    if fault == "missing":
        receipt.unlink()
    elif fault == "truncated":
        receipt.write_bytes(receipt.read_bytes()[:-1])
    elif fault == "same-byte-replacement":
        peer = context.parent / "peer"
        peer.write_bytes(receipt.read_bytes())
        os.replace(peer, receipt)
    elif fault == "directory":
        receipt.unlink()
        receipt.mkdir()
    elif fault == "symlink":
        original = receipt.with_name("original")
        receipt.rename(original)
        receipt.symlink_to(original)
    elif fault == "public-mode":
        receipt.chmod(0o666)
    else:
        rows = [json.loads(line) for line in receipt.read_text().splitlines()]
        if fault == "schema":
            rows[0]["version"] = True
        elif fault == "root":
            rows[0]["root"] = str(tmp_path)
        else:
            rows[1]["target"] = str(tmp_path / "outside")
        receipt.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = {path: path.read_bytes() for path in (root / "payload-dir", root / "stamped-version")}
    with pytest.raises((proof.PublicationProofError, OSError)):
        proof.recover_owned(context, [receipt], list(before), context.parent / "recovery.jsonl", expected(root, payload))
    assert {path: path.read_bytes() for path in before} == before
    if fault != "truncated":
        with pytest.raises((proof.PublicationProofError, OSError)):
            proof.read_receipt(receipt, root, set(before), recovery_only=True)


@pytest.mark.parametrize("tail", [b"", b'{"kind": "finish",', b"\xff\xfe"])
def test_recovery_only_accepts_durable_created_prefix_without_certifying_success(tmp_path: Path, tail: bytes) -> None:
    root, context, payload, _bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    rows = receipt.read_bytes().splitlines(keepends=True)
    receipt.write_bytes(b"".join(rows[:-1]) + tail)
    before = proof.state_path(receipt, private=True)
    with pytest.raises(proof.PublicationProofError):
        proof.read_receipt(receipt, root, {root / "payload-dir", root / "stamped-version"})
    entries = proof.read_context(context)[1]
    recovered = proof._proofs([receipt], root, entries, expected(root, payload), recovery_only=True)
    assert all(proof.state_path(path) in recovered[path] for path in entries)
    assert proof.state_path(receipt, private=True) == before
    with pytest.raises(proof.PublicationProofError, match="not declared"):
        proof._proofs([receipt], root, entries, [[str(root / "payload-dir"), "0" * 64]], recovery_only=True)


@pytest.mark.parametrize("fault", ["empty", "header", "sequence", "record", "finish", "oversized"])
def test_recovery_prefix_never_repairs_missing_or_invalid_complete_records(tmp_path: Path, fault: str) -> None:
    root, context, payload, _bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    rows = [json.loads(line) for line in receipt.read_bytes().splitlines()]
    if fault == "empty":
        data = b""
    elif fault == "header":
        data = json.dumps(rows[0]).encode()[:20]
    elif fault == "oversized":
        data = receipt.read_bytes() + b"x" * proof.MAX_BYTES
    else:
        if fault == "sequence":
            rows[1]["sequence"] = 2
        elif fault == "record":
            rows[1]["unexpected"] = True
        else:
            rows[-1]["sequence"] += 1
        data = "".join(json.dumps(row) + "\n" for row in rows).encode()
    receipt.write_bytes(data)
    before = receipt.read_bytes()
    with pytest.raises(proof.PublicationProofError):
        proof.read_receipt(receipt, root, {root / "payload-dir", root / "stamped-version"}, recovery_only=True)
    assert receipt.read_bytes() == before


def test_failed_producer_records_owned_recovery_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, context, payload, bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    actual = os.replace
    fired = False

    def fail_second(old: Path, new: Path, **kwargs: object) -> None:
        nonlocal fired
        if Path(new) == root / "stamped-version" and not fired:
            fired = True
            raise OSError("injected producer failure")
        actual(old, new, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_second)
        with pytest.raises(OSError, match="injected"):
            publish(root, payload, receipt)
    wanted = expected(root, payload)
    proof.check_owned(context, [receipt], [root / "payload-dir"], wanted)
    rows = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert rows[-1]["outcome"] == "error"
    assert any(row.get("phase") == "recovery" for row in rows)
    output = context.parent / "outer-recovery.jsonl"
    proof.recover_owned(context, [receipt], [root / "payload-dir"], output, wanted)
    assert (root / "payload-dir").read_bytes() == Path(bindings[0][1]).read_bytes()


@pytest.mark.parametrize("producer", ["activate", "session-stamp"])
def test_actual_producer_identity_not_post_return_peer(tmp_path: Path, producer: str) -> None:
    names = ("current-version", "last-known-good") if producer == "activate" else ("launcher",)
    root, context, _payload, _bindings = case(tmp_path, names)
    receipt = context.parent / "producer.jsonl"
    if producer == "activate":
        (root / "versions/2.0.0").mkdir(parents=True)
        runtime.activate(root, "2.0.0", link_free=True, ownership_receipt=receipt)
        target, data = root / "current-version", b"2.0.0\n"
    else:
        target, data = root / "launcher", b"new launcher\n"
        command = [sys.executable, "-I", "-B", str(REPOSITORY / "plugins/agent-index/scripts/session-stamp.py"),
                   "publish", str(target), "metadata", "--ownership-receipt", str(receipt),
                   "--publication-root", str(root)]
        result = subprocess.run(command, input=data, capture_output=True, timeout=10, creationflags=HEADLESS)
        assert result.returncode == 0, result.stderr
    declared = [[str(root / name), hashlib.sha256(data).hexdigest()] for name in names]
    proof.check_owned(context, [receipt], [target], declared)
    peer = root / "peer"
    peer.write_bytes(target.read_bytes())
    os.replace(peer, target)
    with pytest.raises(proof.PublicationProofError, match="producer-created"):
        proof.check_owned(context, [receipt], [target], declared)


@pytest.mark.parametrize("noop", [False, True])
def test_default_session_writer_serializes_with_redirect_publication(tmp_path: Path, noop: bool) -> None:
    root, _context, _payload, _bindings = case(tmp_path, ("payload-dir",))
    target = root / "payload-dir"
    before = target.read_bytes()
    entry = REPOSITORY / "plugins/agent-index/scripts/session-stamp.py"
    barrier = tmp_path / "attempted"
    program = """
from contextlib import contextmanager
from pathlib import Path
import runpy, sys
entry, target, barrier = sys.argv[1:]
sys.path.insert(0, str(Path(entry).parent))
import stamp_paths
component = stamp_paths.publication_component()
original = component.target_leases
@contextmanager
def attempted(root, targets, **kwargs):
    Path(barrier).write_text('ready')
    with original(root, targets, **kwargs):
        yield
component.target_leases = attempted
sys.argv = [entry, 'publish', target, 'metadata']
runpy.run_path(entry, run_name='__main__')
"""
    command = [sys.executable, "-I", "-B", "-c", program, str(entry), str(target), str(barrier)]
    child = None
    try:
        with proof.target_leases(root, [target]):
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=HEADLESS)
            child.stdin.write(before if noop else b"serialized\n")
            child.stdin.close()
            child.stdin = None
            deadline = time.monotonic() + 5
            while not barrier.exists():
                assert child.poll() is None
                assert time.monotonic() < deadline, "Default producer did not reach its actual lease"
                time.sleep(0.01)
            time.sleep(0.05)
            if child.poll() is not None:
                output, error = child.communicate(timeout=10)
                pytest.fail(f"Default producer exited before lease release: {output!r} {error!r}")
            assert target.read_bytes() == before
        output, error = child.communicate(timeout=10)
        assert child.returncode == 0, output + error
        assert target.read_bytes() == (before if noop else b"serialized\n")
    finally:
        if child is not None and child.poll() is None:
            child.communicate(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX creation modes")
def test_publication_lease_storage_is_private_with_permissive_umask(tmp_path: Path) -> None:
    root, _context, _payload, _bindings = case(tmp_path, ("payload-dir",))
    previous = os.umask(0)
    try:
        with proof.target_leases(root, [root / "payload-dir"]):
            directory = root / ".publication-leases"
            assert directory.stat().st_mode & 0o777 == 0o700
            assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    finally:
        os.umask(previous)


def test_failed_activation_keeps_created_selector_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, context, _payload, bindings = case(tmp_path, ("current-version", "last-known-good"))
    (root / "versions/2.0.0").mkdir(parents=True)
    receipt = context.parent / "activate.jsonl"
    actual = os.replace

    def fail_last(old: Path, new: Path, **kwargs: object) -> None:
        if Path(new) == root / "last-known-good":
            raise OSError("injected selector failure")
        actual(old, new, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_last)
        with pytest.raises(OSError, match="selector"):
            runtime.activate(root, "2.0.0", link_free=True, ownership_receipt=receipt)
    wanted = [[str(root / name), hashlib.sha256(b"2.0.0\n").hexdigest()]
              for name in ("current-version", "last-known-good")]
    proof.check_owned(context, [receipt], [root / "current-version"], wanted)
    proof.recover_owned(context, [receipt], [root / "current-version"], context.parent / "recover.jsonl", wanted)
    assert (root / "current-version").read_bytes() == Path(bindings[0][1]).read_bytes()


def test_failed_session_publisher_after_rename_is_owned(tmp_path: Path) -> None:
    root, context, _payload, bindings = case(tmp_path, ("launcher",))
    target, receipt = root / "launcher", context.parent / "stamp.jsonl"
    entry = REPOSITORY / "plugins/agent-index/scripts/session-stamp.py"
    program = """
import json, os, runpy, sys
from pathlib import Path
entry, target, root, receipt = sys.argv[1:]
sys.path.insert(0, str(Path(entry).parent))
replace = os.replace
def fail_after(old, new, **kwargs):
    rows = [json.loads(line) for line in Path(receipt).read_text().splitlines()]
    created = next(row for row in reversed(rows) if row['kind'] == 'created')
    staged = os.stat(old, dir_fd=kwargs.get('src_dir_fd'))
    assert (created['state']['device'], created['state']['inode']) == (staged.st_dev, staged.st_ino)
    replace(old, new, **kwargs)
    if Path(new).name == Path(target).name:
        raise OSError('injected after session rename')
os.replace = fail_after
sys.argv = [entry, 'publish', target, 'metadata', '--ownership-receipt', receipt, '--publication-root', root]
runpy.run_path(entry, run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(entry), str(target), str(root), str(receipt)],
        input=b"new launcher\n", capture_output=True, timeout=10, creationflags=HEADLESS,
    )
    assert result.returncode != 0 and b"injected after session rename" in result.stderr
    wanted = [[str(target), hashlib.sha256(b"new launcher\n").hexdigest()]]
    proof.check_owned(context, [receipt], [target], wanted)
    proof.recover_owned(context, [receipt], [target], context.parent / "recover.jsonl", wanted)
    assert target.read_bytes() == Path(bindings[0][1]).read_bytes()


def test_default_cli_shapes_are_unchanged(tmp_path: Path) -> None:
    root, context, payload, _bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    command = [sys.executable, "-I", "-B", str(LIBRARY / "versioned_runtime.py"), "--root", str(root)]
    quiet = subprocess.run(
        [*command, "publish-redirects", "--payload-dir", str(payload), "--version", "2.0.0"],
        capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )
    assert quiet.returncode == 0 and quiet.stdout == "", quiet.stderr
    result = subprocess.run(
        [*command, "--json", "publish-redirects", "--payload-dir", str(payload), "--version", "2.0.0",
         "--ownership-receipt", str(context.parent / "shape.jsonl")],
        capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"published": {"payload-dir": str(payload.resolve()), "stamped-version": "2.0.0"}}
    (root / "versions/2.0.0").mkdir(parents=True)
    activated = subprocess.run(
        [*command, "--json", "activate", "2.0.0", "--no-link"],
        capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )
    assert activated.returncode == 0, activated.stderr
    assert json.loads(activated.stdout) == {"activated": "2.0.0", "path": str(root / "versions/2.0.0")}


@pytest.mark.parametrize("producer", ["redirects", "activate"])
def test_created_proof_is_fsynced_before_actual_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, producer: str) -> None:
    names = ("payload-dir", "stamped-version") if producer == "redirects" else ("current-version", "last-known-good")
    root, context, payload, _bindings = case(tmp_path, names)
    receipt = context.parent / "producer.jsonl"
    actual_replace, actual_sync = os.replace, os.fsync
    synced: set[tuple[int, int]] = set()
    seen = []

    def sync(fd: int) -> None:
        actual_sync(fd)
        info = os.fstat(fd)
        synced.add((info.st_ino, info.st_size))

    def replace(old: Path, new: Path, **kwargs: object) -> None:
        if Path(new) in {root / name for name in names}:
            rows = [json.loads(line) for line in receipt.read_text().splitlines()]
            entry = next(row for row in reversed(rows) if row.get("target") == str(new))
            staged = proof.state_path(Path(old))
            assert proof.PhysicalState.parse(entry["state"]) == staged
            info = receipt.stat()
            assert (info.st_ino, info.st_size) in synced
            seen.append(Path(new))
        actual_replace(old, new, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        patch.setattr(os, "replace", replace)
        if producer == "redirects":
            publish(root, payload, receipt)
        else:
            (root / "versions/2.0.0").mkdir(parents=True)
            runtime.activate(root, "2.0.0", link_free=True, ownership_receipt=receipt)
    assert set(seen) == {root / name for name in names}


def test_native_recovery_partial_error_has_retryable_created_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, context, payload, bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    wanted = expected(root, payload)
    targets = [root / "payload-dir", root / "stamped-version"]
    recovery = context.parent / "partial-recovery.jsonl"
    actual = os.replace

    def fail_second(old: Path, new: Path, **kwargs: object) -> None:
        if Path(new) == targets[1]:
            raise OSError("injected recovery failure")
        actual(old, new, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_second)
        with pytest.raises(OSError, match="recovery"):
            proof.recover_owned(context, [receipt], targets, recovery, wanted)
    proof.check_owned(context, [receipt, recovery], targets, wanted)
    proof.recover_owned(context, [receipt, recovery], targets, context.parent / "retry.jsonl", wanted)
    assert [target.read_bytes() for target in targets] == [Path(item[1]).read_bytes() for item in bindings]


def test_native_backup_corruption_refuses_before_any_recovery(tmp_path: Path) -> None:
    root, context, payload, bindings = case(tmp_path, ("payload-dir", "stamped-version"))
    receipt = context.parent / "producer.jsonl"
    publish(root, payload, receipt)
    targets = [root / "payload-dir", root / "stamped-version"]
    before = [path.read_bytes() for path in targets]
    Path(bindings[1][1]).write_bytes(b"corrupt")
    with pytest.raises(proof.PublicationProofError, match="backup"):
        proof.recover_owned(context, [receipt], targets, context.parent / "failed.jsonl", expected(root, payload))
    assert [path.read_bytes() for path in targets] == before
