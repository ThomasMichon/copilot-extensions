"""Create-once numbered completion, including real competing publishers."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"
spec = importlib.util.spec_from_file_location("publication_runtime", SOURCE)
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


@pytest.mark.guard
@pytest.mark.parametrize("payload_hash", [None, "same"])
def test_numbered_completion_reuse_preserves_identity(tmp_path, payload_hash):
    marker = vr.mark_complete(tmp_path, "1.0", payload_hash=payload_hash, pid=11)
    before = marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns
    assert vr.mark_complete(tmp_path, "1.0", payload_hash=payload_hash, pid=22) == marker
    assert (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns) == before
    assert vr.read_marker(tmp_path, "1.0")["pid"] == 11


@pytest.mark.guard
@pytest.mark.parametrize("original,requested", [(None, "new"), ("old", None), ("old", "new")])
def test_numbered_completion_refuses_content_conflict(tmp_path, original, requested):
    marker = vr.mark_complete(tmp_path, "1.0", payload_hash=original)
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    with pytest.raises(RuntimeError, match="content-conflict"):
        vr.mark_complete(tmp_path, "1.0", payload_hash=requested)
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert list(marker.parent.glob("*.tmp-*")) == []


@pytest.mark.parametrize("evidence", [
    b"{broken", b"\x80", b'{"version":"1.0","version":"1.0"}',
    b'{"version":"other","completed_at":"x","pid":1}',
])
def test_numbered_completion_preserves_malformed_evidence(tmp_path, evidence):
    marker = vr.marker_path(tmp_path, "1.0")
    marker.parent.mkdir(parents=True)
    marker.write_bytes(evidence)
    with pytest.raises(RuntimeError, match="health-repair-required"):
        vr.mark_complete(tmp_path, "1.0", payload_hash="same")
    assert marker.read_bytes() == evidence


@pytest.mark.guard
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires a POSIX FIFO")
@pytest.mark.timeout(5)
def test_publication_refuses_fifo_evidence_without_blocking(tmp_path):
    marker = vr.marker_path(tmp_path, "1.0")
    marker.parent.mkdir(parents=True)
    os.mkfifo(marker)
    before = marker.stat().st_ino
    with pytest.raises(RuntimeError, match="health-repair-required"):
        vr.mark_complete(tmp_path, "1.0", payload_hash="same")
    assert marker.stat().st_ino == before


@pytest.mark.parametrize("fields", [
    {"pid": True}, {"pid": -1}, {"pid": "one"}, {"payload_hash": 7},
])
def test_invalid_publication_does_not_create_state(tmp_path, fields):
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="invalid completion"):
        vr.mark_complete(root, "1.0", **fields)
    assert not root.exists()


@pytest.mark.parametrize("version", ["DEV", "Dev", "dEv"])
def test_dev_case_alias_cannot_bypass_explicit_claim(tmp_path, version):
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match="must be named 'dev'"):
        vr.mark_complete(root, version, payload_hash="same")
    assert not root.exists()


def test_read_marker_surfaces_actual_storage_failure(tmp_path, monkeypatch):
    vr.mark_complete(tmp_path, "1.0")

    def denied(path):
        raise PermissionError("denied completion evidence")

    monkeypatch.setattr(vr, "_load_unique_json", denied)
    with pytest.raises(PermissionError, match="denied completion"):
        vr.read_marker(tmp_path, "1.0")
    with pytest.raises(PermissionError, match="denied completion"):
        vr.is_complete(tmp_path, "1.0")


@pytest.mark.parametrize("operation", ["link", "fsync"])
def test_failed_publication_leaves_no_completion_or_temporary(tmp_path, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise OSError("publication storage failure")

    monkeypatch.setattr(vr.os, operation, fail)
    with pytest.raises(OSError, match="publication storage failure"):
        vr.mark_complete(tmp_path, "1.0", payload_hash="same")
    assert not vr.marker_path(tmp_path, "1.0").exists()
    assert list(vr.version_dir(tmp_path, "1.0").glob("*.tmp-*")) == []


def test_claimed_dev_completion_remains_mutable(tmp_path):
    with pytest.raises(RuntimeError, match="explicit dev claim"):
        vr.mark_complete(tmp_path, "dev", payload_hash="old")
    assert not vr.version_dir(tmp_path, "dev").exists()
    vr.claim_dev(tmp_path, owner=str(tmp_path))
    marker = vr.mark_complete(tmp_path, "dev", payload_hash="old", pid=11)
    assert vr.mark_complete(tmp_path, "dev", payload_hash="new", pid=22) == marker
    assert vr.read_marker(tmp_path, "dev")["payload_hash"] == "new"
    assert vr.read_marker(tmp_path, "dev")["pid"] == 22


@pytest.mark.parametrize("winner", ["same", "different", "malformed"])
def test_link_collision_revalidates_without_overwrite(tmp_path, monkeypatch, winner):
    marker = vr.marker_path(tmp_path, "1.0")

    def collide(source, destination):
        evidence = (
            "{broken" if winner == "malformed" else
            json.dumps({"version": "1.0", "completed_at": "winner", "pid": 11,
                        "payload_hash": winner})
        )
        destination.write_text(evidence, encoding="utf-8")
        raise FileExistsError("another publisher won")

    monkeypatch.setattr(vr.os, "link", collide)
    if winner == "same":
        assert vr.mark_complete(tmp_path, "1.0", payload_hash="same") == marker
        assert vr.read_marker(tmp_path, "1.0")["pid"] == 11
    else:
        with pytest.raises(RuntimeError, match="raced conflicting evidence"):
            vr.mark_complete(tmp_path, "1.0", payload_hash="same")
        if winner == "malformed":
            assert marker.read_text(encoding="utf-8") == "{broken"
        else:
            assert vr.read_marker(tmp_path, "1.0")["payload_hash"] == winner
    assert list(marker.parent.glob("*.tmp-*")) == []


@pytest.mark.guard
def test_fresh_standalone_cli_publication_reuses_then_refuses(tmp_path):
    standalone = tmp_path / "standalone.py"
    shutil.copyfile(SOURCE, standalone)
    for name in ("versioned_runtime_leases.py", "versioned_runtime_construction.py"):
        shutil.copyfile(SOURCE.with_name(name), tmp_path / name)
    root = tmp_path / "runtime"

    def publish(digest):
        return subprocess.run(
            [sys.executable, str(standalone), "--root", str(root), "--json",
             "mark-complete", "1.0", "--payload-hash", digest],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    first = publish("same")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["marked"] == "1.0"
    marker = vr.marker_path(root, "1.0")
    before = marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns
    reused = publish("same")
    assert reused.returncode == 0, reused.stderr
    assert json.loads(reused.stdout)["path"] == str(marker)
    conflict = publish("different")
    assert conflict.returncode == 1
    assert "content-conflict" in conflict.stderr
    assert not conflict.stdout
    assert (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns) == before


@pytest.fixture
def processes():
    owned = []
    yield owned
    for process in owned:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def publisher(root, payload_hash, processes):
    script = (
        "import importlib.util, pathlib, json, os\n"
        f"s=importlib.util.spec_from_file_location('vr', {str(SOURCE)!r})\n"
        "vr=importlib.util.module_from_spec(s); s.loader.exec_module(vr)\n"
        f"root=pathlib.Path({str(root)!r})\n"
        "print('ready', flush=True)\n"
        "input()\n"
        "try:\n"
        f" vr.mark_complete(root, '1.0', payload_hash={payload_hash!r})\n"
        " print(json.dumps({'published': True, 'pid': os.getpid(), "
        "'marker': vr.read_marker(root, '1.0')}))\n"
        "except RuntimeError as exc:\n"
        " print(json.dumps({'published': False, 'pid': os.getpid(), 'error': str(exc)}))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    processes.append(process)
    assert process.stdout.readline().strip() == "ready"
    return process


@pytest.mark.guard
@pytest.mark.parametrize("hashes", [("same", "same"), ("first", "second")])
def test_native_publishers_preserve_one_winner(tmp_path, processes, hashes):
    contenders = [publisher(tmp_path, digest, processes) for digest in hashes]
    for process in contenders:
        process.stdin.write("\n")
        process.stdin.flush()
    results = []
    for process in contenders:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        results.append(json.loads(stdout))
    marker = vr.read_marker(tmp_path, "1.0")
    assert marker["pid"] in {result["pid"] for result in results}
    if hashes[0] == hashes[1]:
        assert all(result["published"] for result in results)
        assert all(result["marker"] == marker for result in results)
    else:
        assert sum(result["published"] for result in results) == 1
        loser = next(result for result in results if not result["published"])
        assert "content-conflict" in loser["error"]
    assert list(vr.version_dir(tmp_path, "1.0").glob("*.tmp-*")) == []
